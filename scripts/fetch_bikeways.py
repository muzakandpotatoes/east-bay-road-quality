"""Download the published bikeway layers for Berkeley and Oakland.

Sources differ a lot in quality, so each is kept separately and reconciled in
normalize_bikeways.py:

  Oakland  one maintained network layer on the city's own server, carrying both
           an existing and a proposed Caltrans class per segment.
  Berkeley three partial layers -- the bicycle boulevard network, a small
           separated-bikeway layer, and a bikeway layer whose "existing"
           classes date to 2004 -- so OSM is pulled as well to cover the
           current Class II network the city layers miss.
"""
import json, os, sys, time, urllib.parse, urllib.request
import arcgis

BK = "https://gis.cityofberkeley.info/arcgis/rest/services"
BK_AGOL = "https://services1.arcgis.com/IYiCpZoSIq9lAxi8/arcgis/rest/services"

LAYERS = {
    # Oakland's maintained bikeway network (existing + proposed classes).
    "oakland_bikenetwork.geojson":
        "https://gismaps.oaklandca.gov/server/rest/services/BikeFacilities/FeatureServer/2",
    # Berkeley's live bicycle boulevard table (219 rows; the AGOL copies lag at 211).
    "berkeley_bike_blvd.geojson":
        f"{BK_AGOL}/Low_Stress_Bikeway_Network/FeatureServer/2",
    # Berkeley's separated bikeways (Class IV).
    "berkeley_separated.geojson":
        f"{BK_AGOL}/Low_Stress_Bikeway_Network/FeatureServer/4",
    # Berkeley's older bikeway layer: proposed class plus an "existing in 2004" class.
    "berkeley_bikeways.geojson":
        f"{BK}/Public/Portal_Transportation/MapServer/1",
}

OVERPASS = ("https://overpass.kumi.systems/api/interpreter",
            "https://overpass-api.de/api/interpreter")
UA = "east-bay-road-quality/1.0 (bikeway mapping)"
# Split by city: a single request covering both times out on the public mirrors.
# Berkeley's boundary reaches -122.369 at the Marina (where the Bay Trail
# runs) and 37.836 at the southern edge; a tighter box silently loses paths.
OSM_AREAS = {"berkeley": "37.830,-122.375,37.912,-122.228",
             "oakland_north": "37.78,-122.30,37.86,-122.16",
             "oakland_south": "37.69,-122.25,37.79,-122.11"}
OSM_QUERY = """[out:json][timeout:600];
(
  way["highway"="cycleway"](%s);
  way[~"^(cycleway|cycleway:both|cycleway:left|cycleway:right|bicycle_road|cyclestreet)$"~"."](%s);
);
out geom;"""


def fetch_osm(bbox):
    body = urllib.parse.urlencode({"data": OSM_QUERY % (bbox, bbox)}).encode()
    for attempt in range(2):
        for url in OVERPASS:
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=650) as fh:
                    return json.load(fh)
            except Exception as exc:
                print(f"      {url.split('/')[2]}: {exc}")
        time.sleep(30 * (attempt + 1))
    return None


def osm_features(data):
    out = []
    for el in (data or {}).get("elements", []):
        geom, tags = el.get("geometry"), el.get("tags") or {}
        if not geom or len(geom) < 2:
            continue
        out.append({"type": "Feature",
                    "geometry": {"type": "LineString",
                                 "coordinates": [[p["lon"], p["lat"]] for p in geom]},
                    "properties": {"osm_id": el.get("id"), **tags}})
    return out


def main(raw_dir):
    os.makedirs(raw_dir, exist_ok=True)
    for name, url in LAYERS.items():
        path = os.path.join(raw_dir, name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"  {name}: already present")
            continue
        print(f"  {name}:")
        json.dump(arcgis.fetch_geojson(url), open(path, "w"))

    path = os.path.join(raw_dir, "osm_bikeways.geojson")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print("  osm_bikeways.geojson: already present")
        return
    feats, seen = [], set()
    for area, bbox in OSM_AREAS.items():
        print(f"  OSM {area}:")
        data = fetch_osm(bbox)
        if data is None:
            print(f"      skipped -- Overpass unavailable")
            continue
        for f in osm_features(data):
            if f["properties"]["osm_id"] not in seen:     # bboxes overlap slightly
                seen.add(f["properties"]["osm_id"])
                feats.append(f)
        print(f"      {len(feats)} ways so far")
    json.dump({"type": "FeatureCollection", "features": feats}, open(path, "w"))
    print(f"  osm_bikeways.geojson: {len(feats)} ways")


if __name__ == "__main__":
    main(sys.argv[1])
