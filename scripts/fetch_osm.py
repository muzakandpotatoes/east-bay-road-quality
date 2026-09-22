"""Fetch Berkeley street geometry from OpenStreetMap as a centerline fallback.

Berkeley's own centerline layer is missing a handful of streets the PCI table
references -- mostly ones straddling the Oakland border (62nd-67th) and recent
renames (Kala Bagai Way).  Only names absent from the city layer are used, so
OSM never overrides the authoritative geometry.
"""
import json, sys, time, urllib.parse, urllib.request

# Overpass mirrors return 504 under load; try them in turn.
ENDPOINTS = ("https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.osm.ch/api/interpreter")
UA = "east-bay-road-quality/1.0 (pavement condition mapping)"
BBOX = (37.835, -122.345, 37.915, -122.225)      # Berkeley plus a border margin
QUERY = """[out:json][timeout:180];
way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|road)$"]["name"]
  (%s,%s,%s,%s);
out geom;""" % BBOX


def query():
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    last = None
    for attempt in range(2):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=300) as fh:
                    return json.load(fh)
            except Exception as exc:
                last = f"{url}: {exc}"
                print(f"    {last}")
        time.sleep(20 * (attempt + 1))
    raise SystemExit(f"Overpass unavailable ({last}); rerun later, the rest of "
                     "the pipeline works without it")


def main(out_path):
    data = query()

    feats = []
    for el in data.get("elements", []):
        name = (el.get("tags") or {}).get("name")
        geom = el.get("geometry")
        if not name or not geom or len(geom) < 2:
            continue
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [[p["lon"], p["lat"]] for p in geom]},
                      "properties": {"FULLNAME": name, "SOURCE": "OSM"}})
    json.dump({"type": "FeatureCollection", "features": feats}, open(out_path, "w"))
    print(f"  OSM: {len(feats)} named ways -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1])
