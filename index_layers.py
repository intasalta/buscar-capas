"""
Script de consola para indexar capas WMS de INTA y guardarlas en layers_cache.json.
Ejecutar con:
    python index_layers.py
"""

import os
import sys
import logging
from datetime import datetime

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from wms.indexer import index_all_wms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)

def progress_display(current, total, node_name, count, error):
    status = f"✅ {count} recursos (capas, mapas, dashboards, geohistorias)" if not error else f"❌ Error: {error}"
    print(f"[{current}/{total}] {node_name} -> {status}")

if __name__ == "__main__":
    print("=" * 65)
    print("🌍 INDEXADOR INTEGRAL DE RECURSOS GEOESPACIALES – IDGEO INTA")
    print("=" * 65)
    print("Iniciando consulta respetuosa a los nodos (1 segundo de pausa entre nodos)...")
    print("Indexando capas WMS, mapas interactivos, dashboards y geohistorias.\n")

    # Rutas relativas al script
    base_dir = os.path.dirname(os.path.abspath(__file__))
    sources_file = os.path.join(base_dir, "data.json")
    output_file = os.path.join(base_dir, "layers_cache.json")

    start_time = datetime.now()
    result = index_all_wms(
        sources_path=sources_file,
        output_path=output_file,
        delay_between_requests=1.0,
        progress_callback=progress_display
    )
    elapsed = datetime.now() - start_time
    counts = result.get("counts_by_type", {})

    print("\n" + "=" * 65)
    print(f"🎉 ¡Proceso finalizado en {elapsed.seconds} segundos!")
    print(f"📊 Total de recursos indexados: {result.get('total_resources', result.get('total_layers'))}")
    print(f"   • Vector:        {counts.get('VECTOR', 0)}")
    print(f"   • Raster:        {counts.get('RASTER', 0)}")
    print(f"   • Mapas:         {counts.get('MAPA', 0)}")
    print(f"   • Dashboards:    {counts.get('DASHBOARD', 0)}")
    print(f"   • GeoHistorias:  {counts.get('GEOHISTORIA', 0)}")
    print(f"💾 Archivo generado: {output_file}")
    print("=" * 65)

