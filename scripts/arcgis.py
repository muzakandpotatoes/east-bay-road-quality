"""Minimal paging client for ArcGIS REST feature services.

Services cap a single response at maxRecordCount features, so results are
pulled in OBJECTID-ordered pages via resultOffset.
"""
import json, time, urllib.parse, urllib.request

UA = "east-bay-road-quality/1.0 (pavement condition mapping)"


def _get(url, params, retries=3, post=False):
    """GET, or POST the parameters as a form body when the query is long."""
    body = urllib.parse.urlencode(params).encode()
    if post or len(body) > 1500:
        req = urllib.request.Request(url, data=body, headers={
            "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    else:
        req = urllib.request.Request(f"{url}?{body.decode()}", headers={"User-Agent": UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"ArcGIS error: {data['error']}")
            return data
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def layer_info(layer_url):
    return _get(layer_url, {"f": "json"})


def count(layer_url, where="1=1"):
    return _get(layer_url + "/query", {"where": where, "returnCountOnly": "true", "f": "json"})["count"]


def object_ids(layer_url, where="1=1"):
    d = _get(layer_url + "/query", {"where": where, "returnIdsOnly": "true", "f": "json"})
    return d.get("objectIds") or d.get("properties", {}).get("objectIds") or []


def fetch_geojson(layer_url, where="1=1", out_fields="*", page=None, out_sr=4326):
    """Return a GeoJSON FeatureCollection with every matching feature.

    Fetches by explicit objectId batches rather than resultOffset paging: some
    MapServer endpoints silently stop returning rows past a few thousand rows
    of offset, which truncates the layer without any error.
    """
    info = layer_info(layer_url)
    page = page or min(info.get("maxRecordCount", 1000), 500)
    oid_field = info.get("objectIdField", "OBJECTID")
    ids = sorted(object_ids(layer_url, where))

    def grab(batch):
        return _get(layer_url + "/query", {
            "objectIds": ",".join(str(x) for x in batch),
            "outFields": out_fields, "returnGeometry": "true",
            "outSR": out_sr, "f": "geojson", "orderByFields": oid_field,
        }).get("features", [])

    features, skipped = [], []
    def take(batch):
        """Fetch a batch, bisecting on failure to isolate unconvertible rows."""
        try:
            features.extend(grab(batch))
        except Exception:
            if len(batch) == 1:
                skipped.append(batch[0])       # e.g. null or non-convertible geometry
                return
            mid = len(batch) // 2
            take(batch[:mid])
            take(batch[mid:])

    for i in range(0, len(ids), page):
        take(ids[i:i + page])
        print(f"    {len(features)}/{len(ids)}", end="\r", flush=True)
    # Some rows fail the server's GeoJSON conversion but serialise fine as
    # esriJSON, so recover those individually rather than dropping them.
    recovered, lost = 0, []
    for oid in skipped:
        try:
            data = _get(layer_url + "/query", {
                "objectIds": str(oid), "outFields": out_fields,
                "returnGeometry": "true", "outSR": out_sr, "f": "json"})
            for feat in data.get("features", []):
                geom = _esri_polyline_to_geojson(feat.get("geometry"))
                if geom:
                    features.append({"type": "Feature", "geometry": geom,
                                     "properties": feat.get("attributes", {})})
                    recovered += 1
        except Exception:
            lost.append(oid)

    features = [f for f in features if f.get("geometry")]
    note = f", {recovered} recovered" if recovered else ""
    note += f", LOST {lost}" if lost else ""
    print(f"    {len(features)}/{len(ids)} features{note}        ")
    return {"type": "FeatureCollection", "features": features}


def _esri_polyline_to_geojson(geom):
    paths = (geom or {}).get("paths") or []
    rings = [[[pt[0], pt[1]] for pt in path] for path in paths if len(path) > 1]
    if not rings:
        return None
    if len(rings) == 1:
        return {"type": "LineString", "coordinates": rings[0]}
    return {"type": "MultiLineString", "coordinates": rings}
