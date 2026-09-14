import json
import time
import os
import logging
from datetime import datetime

from wms.capabilities import get_layers_from_wms
from wms.loader import load_wms_sources

def build_geonode_urls(wms_url, layer_name):
    """
    Construye las URLs estándar para GeoNode (dataset público, metadatos y visor WMS).
    No realiza peticiones de red, por lo que es instantáneo y seguro.
    """
    base = wms_url.split("/geoserver")[0].rstrip("/")
    parts = layer_name.split(":")
    if len(parts) == 2:
        workspace, name = parts
    else:
        workspace, name = "", layer_name

    dataset_url = f"{base}/datasets/{layer_name}"
    catalogue_url = f"{base}/catalogue/#/dataset/{layer_name}"
    metadata_url = f"{base}/datasets/{layer_name}/metadata_detail"
    wms_preview_url = (
        f"{wms_url}?service=WMS&version=1.1.0&request=GetMap"
        f"&layers={layer_name}&styles=&width=768&height=400&srs=EPSG:4326&format=image%2Fpng"
    )

    return {
        "base_url": base,
        "dataset_url": dataset_url,
        "catalogue_url": catalogue_url,
        "metadata_url": metadata_url,
        "wms_preview_url": wms_preview_url,
    }

logger = logging.getLogger(__name__)

CACHE_FILE_DEFAULT = "layers_cache.json"

def guess_layer_type(layer_name, title="", abstract=""):
    """
    Infiere de forma heurística si una capa es RASTER o VECTOR
    sin realizar peticiones HTTP adicionales.
    """
    text = f"{layer_name} {title} {abstract}".lower()
    raster_keywords = [
        "raster", "dem", "elevation", "elevacion", "ndvi", "landsat",
        "sentinel", "modis", "temperatura", "precipitacion", "satelital",
        "mosaico", "geotiff", "tiff", "indice"
    ]
    if any(k in text for k in raster_keywords):
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
            layers_found = get_layers_from_wms(node_url, timeout=15, raise_on_error=True)

            
            for layer in layers_found:
                layer_name = layer["name"]
                title = layer["title"]
                abstract = layer.get("abstract") or ""
                
                urls = build_geonode_urls(node_url, layer_name)
                layer_type = guess_layer_type(layer_name, title, abstract)

                all_layers.append({
                    "layer_name": layer_name,
                    "title": title,
                    "abstract": abstract,
                    "keywords": layer.get("keywords", []),
                    "bbox": layer.get("bbox"),
                    "wms_url": node_url,
                    "node": node_name,
                    "node_id": node_id,
                    "layer_type": layer_type,
                    "dataset_url": urls["dataset_url"],
                    "catalogue_url": urls["catalogue_url"],
                    "metadata_url": urls["metadata_url"],
                    "wms_preview_url": urls["wms_preview_url"],
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