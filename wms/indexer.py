import json
import time
import os
import logging
from datetime import datetime

from wms.capabilities import get_layers_from_wms
from wms.loader import load_wms_sources

import re
import unicodedata
import requests

logger = logging.getLogger(__name__)

CACHE_FILE_DEFAULT = "layers_cache.json"

DEFAULT_REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html,*/*",
}

def normalize_text(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")

def fetch_geonode_resources(node_url, timeout=12):
    """
    Consulta la API v2 de GeoNode para obtener datasets del nodo:
    - subtype (raster vs vector 100% exacto)
    - detail_url (enlace directo al catálogo /catalogue/#/dataset/{id})
    - embed_url (visor interactivo /datasets/{layer_name}/embed)
    - thumbnail_url
    """
    base = node_url.split("/geoserver")[0].rstrip("/")
    resources_map = {}
    page = 1
    page_size = 50

    try:
        while True:
            api_url = f"{base}/api/v2/resources?filter{{resource_type}}=dataset&page_size={page_size}&page={page}"
            r = requests.get(api_url, headers=DEFAULT_REQUEST_HEADERS, timeout=timeout)
            if r.status_code != 200:
                break
            data = r.json()
            items = data.get("resources", [])
            if not items:
                break

            for res in items:
                alternate = res.get("alternate") or ""
                name = res.get("name") or ""
                pk = res.get("pk")
                subtype_raw = (res.get("subtype") or "").upper()
                subtype = "RASTER" if subtype_raw == "RASTER" else ("VECTOR" if subtype_raw == "VECTOR" else None)

                detail_url = res.get("detail_url") or (f"{base}/catalogue/#/dataset/{pk}" if pk else f"{base}/catalogue/#/dataset/{alternate}")
                embed_url = res.get("embed_url") or f"{base}/datasets/{alternate}/embed"
                if embed_url:
                    embed_url = embed_url.rstrip("/#").rstrip("/")
                    if not embed_url.endswith("/embed"):
                        embed_url = f"{base}/datasets/{alternate}/embed"

                entry = {
                    "pk": pk,
                    "subtype": subtype,
                    "detail_url": detail_url,
                    "embed_url": embed_url,
                    "thumbnail_url": res.get("thumbnail_url"),
                    "metadata_url": f"{base}/datasets/{alternate}/metadata_detail",
                }

                if alternate:
                    resources_map[alternate] = entry
                    if ":" in alternate:
                        resources_map[alternate.split(":", 1)[1]] = entry
                if name:
                    resources_map[name] = entry

            total = data.get("total", 0)
            if page * page_size >= total or page >= 10:
                break
            page += 1

    except Exception as e:
        logger.warning(f"No se pudo consultar API GeoNode en {base}: {e}")

    return resources_map

def build_geonode_urls(wms_url, layer_name):
    """
    Construye las URLs estándar para GeoNode cuando no se dispone de la API.
    """
    base = wms_url.split("/geoserver")[0].rstrip("/")
    dataset_url = f"{base}/catalogue/#/dataset/{layer_name}"
    catalogue_url = f"{base}/catalogue/#/dataset/{layer_name}"
    embed_url = f"{base}/datasets/{layer_name}/embed"
    metadata_url = f"{base}/datasets/{layer_name}/metadata_detail"
    wms_preview_url = (
        f"{wms_url}?service=WMS&version=1.1.0&request=GetMap"
        f"&layers={layer_name}&styles=&width=768&height=400&srs=EPSG:4326&format=image%2Fpng"
    )

    return {
        "base_url": base,
        "dataset_url": dataset_url,
        "catalogue_url": catalogue_url,
        "embed_url": embed_url,
        "metadata_url": metadata_url,
        "wms_preview_url": wms_preview_url,
    }

def guess_layer_type(layer_name, title="", abstract="", keywords=None):
    """
    Infiere de forma heurística si una capa es RASTER o VECTOR
    analizando el nombre técnico, título, resumen y palabras clave.
    """
    kw_list = keywords or []
    kw_text = " ".join(kw_list)
    full_text = normalize_text(f"{layer_name} {title} {abstract} {kw_text}")

    # 1. Indicadores directos de formato ráster (WCS, GeoTIFF)
    if any(k in kw_list for k in ["WCS", "GeoTIFF", "TIFF", "wcs", "geotiff"]):
        return "RASTER"

    # 2. GeoNode asigna hashes MD5 de 32 caracteres a los archivos ráster subidos
    if re.search(r'_[0-9a-f]{32}', layer_name):
        return "RASTER"

    # 3. Palabras clave temáticas ráster (clima, hidrología, sensores, índices)
    raster_keywords = [
        "raster", "dem", "mde", "elevation", "elevacion", "pendiente", "curvas de nivel",
        "topografia", "ndvi", "evi", "savi", "ndwi", "landsat", "sentinel", "modis",
        "temperatura", "precipitacion", "satelital", "mosaico", "geotiff", "tiff",
        "indice", "hidrico", "deficits", "deficit", "excesos", "exceso", "balance",
        "evapotranspiracion", "p-ep", "etp", "heladas", "radiacion", "anomalia",
        "humedad", "sequia", "agua util", "agua disponible", "clima", "grid", "pixel",
        "biomasa", "rendimiento", "cobertura"
    ]
    if any(k in full_text for k in raster_keywords):
        return "RASTER"

    return "VECTOR"


def index_all_wms(
    sources_path="data.json",
    output_path=CACHE_FILE_DEFAULT,
    delay_between_requests=1.0,
    progress_callback=None
):
    """
    Descarga e indexa las capas de todos los nodos WMS configurados.
    
    - Usa pausas (delay_between_requests) para no activar los firewalls / WAF.
    - Soporta progress_callback(index, total, node_name, layers_count, error).
    - Guarda los resultados estructurados en un archivo JSON local.
    """
    sources = load_wms_sources(sources_path)
    total_nodes = len(sources)
    all_layers = []
    nodes_summary = []

    logger.info(f"Iniciando indexación de {total_nodes} nodos WMS...")

    for i, source in enumerate(sources, 1):
        node_name = source["nombre"]
        node_url = source["url"]
        node_id = source.get("id", f"node_{i}")
        error_msg = None
        layers_found = []

        try:
            logger.info(f"[{i}/{total_nodes}] Consultando nodo: {node_name} ({node_url})")
            
            # Consultar datasets de GeoNode API (si está disponible)
            resources_map = fetch_geonode_resources(node_url, timeout=12)
            
            # Consultar capas WMS
            layers_found = get_layers_from_wms(node_url, timeout=15, raise_on_error=True)

            for layer in layers_found:
                layer_name = layer["name"]
                title = layer["title"]
                abstract = layer.get("abstract") or ""
                keywords = layer.get("keywords", [])
                
                # Intentar mapear con recurso en GeoNode
                clean_name = layer_name.split(":", 1)[1] if ":" in layer_name else layer_name
                res_info = resources_map.get(layer_name) or resources_map.get(clean_name)
                
                if res_info:
                    layer_type = res_info["subtype"] or guess_layer_type(layer_name, title, abstract, keywords)
                    dataset_url = res_info["detail_url"]
                    embed_url = res_info["embed_url"]
                    metadata_url = res_info["metadata_url"]
                    thumbnail_url = res_info.get("thumbnail_url")
                else:
                    layer_type = guess_layer_type(layer_name, title, abstract, keywords)
                    urls = build_geonode_urls(node_url, layer_name)
                    dataset_url = urls["dataset_url"]
                    embed_url = urls["embed_url"]
                    metadata_url = urls["metadata_url"]
                    thumbnail_url = None

                wms_preview_url = embed_url

                all_layers.append({
                    "layer_name": layer_name,
                    "title": title,
                    "abstract": abstract,
                    "keywords": keywords,
                    "bbox": layer.get("bbox"),
                    "wms_url": node_url,
                    "node": node_name,
                    "node_id": node_id,
                    "layer_type": layer_type,
                    "dataset_url": dataset_url,
                    "catalogue_url": dataset_url,
                    "embed_url": embed_url,
                    "metadata_url": metadata_url,
                    "thumbnail_url": thumbnail_url,
                    "wms_preview_url": wms_preview_url,
                })

            nodes_summary.append({
                "id": node_id,
                "nombre": node_name,
                "status": "ok",
                "layers_count": len(layers_found)
            })

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error procesando nodo {node_name}: {error_msg}")
            nodes_summary.append({
                "id": node_id,
                "nombre": node_name,
                "status": "error",
                "error": error_msg,
                "layers_count": 0
            })

        if progress_callback:
            progress_callback(i, total_nodes, node_name, len(layers_found), error_msg)

        # Pausa respetuosa para evitar detección de DoS/Scraping por WAF
        if i < total_nodes and delay_between_requests > 0:
            time.sleep(delay_between_requests)

    cache_data = {
        "updated_at": datetime.now().isoformat(),
        "total_layers": len(all_layers),
        "total_nodes": total_nodes,
        "nodes_summary": nodes_summary,
        "layers": all_layers
    }

    # Guardar en archivo local
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

    logger.info(f"Indexación completada: {len(all_layers)} capas guardadas en {output_path}")
    return cache_data


def load_cached_layers(cache_path=CACHE_FILE_DEFAULT):
    """
    Carga el índice de capas desde el archivo local de caché.
    Retorna None si el archivo no existe.
    """
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error leyendo archivo de caché {cache_path}: {e}")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("Iniciando indexador de capas WMS INTA...")
    data = index_all_wms(delay_between_requests=1.0)
    print(f"¡Listo! Se indexaron {data['total_layers']} capas.")