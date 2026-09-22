"""Build Oakland's 5-Year Paving Plan and paving-moratorium overlays.

Both come straight from the city's REST services as geometry, so unlike
Berkeley's (which are PDF street lists) there is nothing to match:

  * the 5-Year Plan rides on the same pavement-section layer as the PCI data,
    in its PLAN_2022 column (5YP_LS local streets / 5YP_MS major streets /
    5YP_NBR neighbourhood);
  * the moratorium lives on the city's own street centerline service, flagged
    with a paving date and a moratorium end date.

The ArcGIS 5YP_Schedule services are a stale 2016-2021 vintage and are not used.
"""
import datetime, json, sys
import arcgis

MORATORIUM = "https://gismaps.oaklandca.gov/server/rest/services/OaklandStreets/FeatureServer/0"
PLAN_LABELS = {"5YP_LS": "Local streets", "5YP_MS": "Major streets",
               "5YP_NBR": "Neighborhood streets"}
TODAY = datetime.date(2026, 9, 19)


def epoch_to_date(ms):
    if not ms:
        return None
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date().isoformat()


def build_plan(sections_geojson, out_path):
    fc = json.load(open(sections_geojson))
    feats = []
    for f in fc["features"]:
        p = f["properties"]
        code = (p.get("PLAN_2022") or "").strip()
        if not code:
            continue
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "layer": "paving_plan", "plan": "5-Year Paving Plan (2022)",
            "category": PLAN_LABELS.get(code, code),
            "street": p.get("STREET"), "from": p.get("BEGLOCATIO"), "to": p.get("ENDLOCATIO"),
            "section_id": f'{p.get("STREETID")}-{p.get("SECTIONID")}',
            "pci": p.get("PCI_2024"), "plan_area": (p.get("PLAN_AREA") or "").strip() or None,
            "council_district": (p.get("CCD") or "").strip() or None,
        }})
    json.dump({"type": "FeatureCollection", "features": feats}, open(out_path, "w"))
    print(f"plan: {len(feats)} sections -> {out_path}")


def build_moratorium(out_path):
    where = "M_DATEEND IS NOT NULL"
    print(f"moratorium: {arcgis.count(MORATORIUM, where)} street segments flagged")
    fc = arcgis.fetch_geojson(
        MORATORIUM, where=where,
        out_fields="OBJECTID,NAME,LOCATION,M_DATEPAVE,M_DATEEND,M_PROJNUM,M_3YP,M_COMMENT")
    feats, active = [], 0
    for f in fc["features"]:
        p = f["properties"]
        ends = epoch_to_date(p.get("M_DATEEND"))
        is_active = bool(ends and datetime.date.fromisoformat(ends) >= TODAY)
        active += is_active
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "layer": "moratorium", "street": p.get("NAME") or p.get("LOCATION"),
            "paved": epoch_to_date(p.get("M_DATEPAVE")), "ends": ends,
            "project": (p.get("M_PROJNUM") or "").strip() or None,
            "program": (p.get("M_3YP") or "").strip() or None,
            "active": is_active,
        }})
    json.dump({"type": "FeatureCollection", "features": feats}, open(out_path, "w"))
    print(f"moratorium: {len(feats)} segments, {active} still active on {TODAY} -> {out_path}")


if __name__ == "__main__":
    build_plan(sys.argv[1], sys.argv[2])
    build_moratorium(sys.argv[3])
