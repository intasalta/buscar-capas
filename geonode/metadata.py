import logging
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# Headers estándar para peticiones a GeoNode
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

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

    # URLs estándar de GeoNode
    dataset_url = f"{base}/datasets/{layer_name}"
    catalogue_url = f"{base}/catalogue/#/dataset/{layer_name}"
    metadata_url = f"{base}/datasets/{layer_name}/metadata_detail"

    # URL directa de preview en WMS
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


def build_geonode_metadata_url(wms_url, layer_name):
    """
    Mantiene compatibilidad hacia atrás devolviendo (vector_url, raster_url).
    """
    base = wms_url.split("/geoserver")[0].rstrip("/")
    parts = layer_name.split(":")
    if len(parts) == 2:
        workspace, name = parts
        vector_url = f"{base}/datasets/geonode_data:{workspace}:{name}/metadata_detail"
        raster_url = f"{base}/datasets/{name}:{workspace}:{name}/metadata_detail"
    else:
        vector_url = f"{base}/datasets/{layer_name}/metadata_detail"
        raster_url = vector_url

    return vector_url, raster_url