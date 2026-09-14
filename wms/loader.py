import json

def load_wms_sources(path="data_wms.json"):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    wms_list = []
    for item in data.get("wms_sources", []):
        wms_list.append({
            "id": item.get("id"),
            "nombre": item.get("nombre"),
            "url": item.get("url")
        })

    return wms_list