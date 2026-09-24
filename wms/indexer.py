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

def safe_request_get(url, headers=None, timeout=15, retries=2, delay=1.0):
    req_headers = headers or DEFAULT_REQUEST_HEADERS
    last_err = None
    for attempt in range(retries):
        try:
            return requests.get(url, headers=req_headers, timeout=timeout)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay)
    raise last_err

def normalize_text(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")

def fetch_geonode_resources(node_url, timeout=15):
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
            r = safe_request_get(api_url, timeout=timeout)
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

def fetch_geonode_apps_and_maps(node_url, node_name, node_id, timeout=15):
    """
    Consulta la API v2 de GeoNode para obtener mapas interactivos, dashboards y geohistorias del nodo.
    """
    base = node_url.split("/geoserver")[0].rstrip("/")
    items = []
    seen_keys = set()

    # 1. Mapas (/api/v2/maps)
    try:
        page = 1
        page_size = 50
        while page <= 10:
            url = f"{base}/api/v2/maps?page_size={page_size}&page={page}"
            r = safe_request_get(url, timeout=timeout)
            if r.status_code != 200:
                break
            data = r.json()
            maps = data.get("maps", [])
            if not maps:
                break
            for m in maps:
                pk = str(m.get("pk") or "")
                key = f"map_{pk}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                title = m.get("title") or m.get("name") or f"Mapa {pk}"
                name = m.get("name") or f"map_{pk}"
                abstract = m.get("raw_abstract") or m.get("abstract") or ""
                keywords = [k.get("name") if isinstance(k, dict) else str(k) for k in m.get("keywords", []) if k]
                detail_url = m.get("detail_url") or f"{base}/catalogue/#/map/{pk}"
                embed_url = m.get("embed_url") or f"{base}/maps/{pk}/embed"
                thumbnail_url = m.get("thumbnail_url") or ""

                if detail_url.startswith("/"):
                    detail_url = f"{base}{detail_url}"
                if embed_url.startswith("/"):
                    embed_url = f"{base}{embed_url}"
                if thumbnail_url and thumbnail_url.startswith("/"):
                    thumbnail_url = f"{base}{thumbnail_url}"

                items.append({
                    "layer_name": name,
                    "title": title,
                    "abstract": abstract,
                    "keywords": keywords,
                    "bbox": None,
                    "wms_url": node_url,
                    "node": node_name,
                    "node_id": node_id,
                    "layer_type": "MAPA",
                    "resource_pk": pk,
                    "dataset_url": detail_url,
                    "catalogue_url": detail_url,
                    "embed_url": embed_url,
                    "metadata_url": f"{base}/maps/{pk}/metadata_detail",
                    "thumbnail_url": thumbnail_url,
                    "wms_preview_url": embed_url,
                })

            total = data.get("total", 0)
            if page * page_size >= total:
                break
            page += 1
    except Exception as e:
        logger.warning(f"Error consultando mapas en {base}: {e}")

    # 2. GeoApps (/api/v2/geoapps - dashboards y geohistorias)
    try:
        page = 1
        page_size = 50
        while page <= 10:
            url = f"{base}/api/v2/geoapps?page_size={page_size}&page={page}"
            r = safe_request_get(url, timeout=timeout)
            if r.status_code != 200:
                break
            data = r.json()
            apps = data.get("geoapps", [])
            if not apps:
                break
            for a in apps:
                pk = str(a.get("pk") or "")
                res_type = (a.get("resource_type") or "").lower()
                title = a.get("title") or a.get("name") or f"App {pk}"
                name = a.get("name") or f"app_{pk}"
                abstract = a.get("raw_abstract") or a.get("abstract") or ""
                keywords = [k.get("name") if isinstance(k, dict) else str(k) for k in a.get("keywords", []) if k]

                if res_type == "dashboard":
                    layer_type = "DASHBOARD"
                    detail_url = a.get("detail_url") or f"{base}/catalogue/#/dashboard/{pk}"
                elif res_type == "geostory":
                    layer_type = "GEOHISTORIA"
                    detail_url = a.get("detail_url") or f"{base}/catalogue/#/geostory/{pk}"
                else:
                    if "dashboard" in title.lower():
                        layer_type = "DASHBOARD"
                        detail_url = a.get("detail_url") or f"{base}/catalogue/#/dashboard/{pk}"
                    else:
                        layer_type = "GEOHISTORIA"
                        detail_url = a.get("detail_url") or f"{base}/catalogue/#/geostory/{pk}"

                key = f"{layer_type}_{pk}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                embed_url = a.get("embed_url") or f"{base}/apps/{pk}/embed"
                thumbnail_url = a.get("thumbnail_url") or ""

                if detail_url.startswith("/"):
                    detail_url = f"{base}{detail_url}"
                if embed_url.startswith("/"):
                    embed_url = f"{base}{embed_url}"
                if thumbnail_url and thumbnail_url.startswith("/"):
                    thumbnail_url = f"{base}{thumbnail_url}"

                items.append({
                    "layer_name": name,
                    "title": title,
                    "abstract": abstract,
                    "keywords": keywords,
                    "bbox": None,
                    "wms_url": node_url,
                    "node": node_name,
                    "node_id": node_id,
                    "layer_type": layer_type,
                    "resource_pk": pk,
                    "dataset_url": detail_url,
                    "catalogue_url": detail_url,
                    "embed_url": embed_url,
                    "metadata_url": detail_url,
                    "thumbnail_url": thumbnail_url,
                    "wms_preview_url": embed_url,
                })

            total = data.get("total", 0)
            if page * page_size >= total:
                break
            page += 1
    except Exception as e:
        logger.warning(f"Error consultando geoapps en {base}: {e}")

    return items


def index_all_wms(
    sources_path="data.json",
    output_path=CACHE_FILE_DEFAULT,
    delay_between_requests=1.0,
    progress_callback=None
):
    """
    Descarga e indexa las capas WMS (vector/ráster), mapas, dashboards y geohistorias
    de todos los nodos configurados.
    
    - Usa pausas (delay_between_requests) para no activar los firewalls / WAF.
    - Soporta progress_callback(index, total, node_name, layers_count, error).
    - Guarda los resultados estructurados en un archivo JSON local.
    """
    sources = load_wms_sources(sources_path)
    total_nodes = len(sources)
    all_layers = []
    nodes_summary = []

    logger.info(f"Iniciando indexación de {total_nodes} nodos IDGEO...")

    for i, source in enumerate(sources, 1):
        node_name = source["nombre"]
        node_url = source["url"]
        node_id = source.get("id", f"node_{i}")
        error_msg = None
        node_resources = []

        try:
            logger.info(f"[{i}/{total_nodes}] Consultando nodo: {node_name} ({node_url})")
            
            # 1. Consultar datasets de GeoNode API (para clasificación exacta vector/raster)
            resources_map = fetch_geonode_resources(node_url, timeout=12)
            
            # 2. Consultar capas WMS del GeoServer
            layers_found = []
            try:
                layers_found = get_layers_from_wms(node_url, timeout=15, raise_on_error=True)
            except Exception as wms_err:
                logger.warning(f"Aviso al consultar WMS de {node_name}: {wms_err}")

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

                node_resources.append({
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

            # 3. Consultar Mapas, Dashboards y GeoHistorias desde GeoNode API
            apps_and_maps = fetch_geonode_apps_and_maps(node_url, node_name, node_id, timeout=10)
            node_resources.extend(apps_and_maps)

            all_layers.extend(node_resources)

            nodes_summary.append({
                "id": node_id,
                "nombre": node_name,
                "status": "ok",
                "layers_count": len(layers_found),
                "apps_maps_count": len(apps_and_maps),
                "total_node_resources": len(node_resources)
            })

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error procesando nodo {node_name}: {error_msg}")
            nodes_summary.append({
                "id": node_id,
                "nombre": node_name,
                "status": "error",
                "error": error_msg,
                "layers_count": 0,
                "apps_maps_count": 0,
                "total_node_resources": 0
            })

        if progress_callback:
            progress_callback(i, total_nodes, node_name, len(node_resources), error_msg)

        # Pausa respetuosa para evitar detección de DoS/Scraping por WAF
        if i < total_nodes and delay_between_requests > 0:
            time.sleep(delay_between_requests)

    # Conteo por tipo de recurso
    counts_by_type = {
        "VECTOR": sum(1 for l in all_layers if (l.get("layer_type") or "").upper() == "VECTOR"),
        "RASTER": sum(1 for l in all_layers if (l.get("layer_type") or "").upper() == "RASTER"),
        "MAPA": sum(1 for l in all_layers if (l.get("layer_type") or "").upper() == "MAPA"),
        "DASHBOARD": sum(1 for l in all_layers if (l.get("layer_type") or "").upper() == "DASHBOARD"),
        "GEOHISTORIA": sum(1 for l in all_layers if (l.get("layer_type") or "").upper() == "GEOHISTORIA"),
    }

    cache_data = {
        "updated_at": datetime.now().isoformat(),
        "total_layers": len(all_layers),
        "total_resources": len(all_layers),
        "counts_by_type": counts_by_type,
        "total_nodes": total_nodes,
        "nodes_summary": nodes_summary,
        "layers": all_layers
    }

    # Guardar en archivo local
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

    logger.info(f"Indexación completada: {len(all_layers)} recursos guardados en {output_path} (Counts: {counts_by_type})")
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