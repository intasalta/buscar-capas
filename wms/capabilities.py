import logging
from owslib.wms import WebMapService

logger = logging.getLogger(__name__)

# User-Agent estándar para evitar bloqueos por WAF/firewall
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/xml,application/xml,application/xhtml+xml,text/html;q=0.9,text/plain;q=0.8,image/png,*/*;q=0.5",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

def get_layers_from_wms(url, timeout=15, headers=None, raise_on_error=False):
    """
    Obtiene el listado de capas de un servicio WMS de forma segura.
    Usa headers de navegador y timeout configurable para evitar baneos de IP.
    """
    req_headers = headers or DEFAULT_HEADERS
    try:
        wms = WebMapService(url, timeout=timeout, headers=req_headers)
        layers = []

        for layer_name in list(wms.contents):
            layer = wms[layer_name]
            
            # Extraer bounding box si está disponible
            bbox = None
            try:
                bbox = layer.boundingBoxWGS84 or layer.boundingBox
            except Exception:
                bbox = None

            layers.append({
                "name": layer_name,
                "title": layer.title or layer_name,
                "abstract": layer.abstract or "",
                "keywords": getattr(layer, "keywords", []) or [],
                "bbox": bbox,
            })
        return layers

    except Exception as e:
        logger.warning(f"Error consultando WMS en {url}: {e}")
        if raise_on_error:
            raise
        return []