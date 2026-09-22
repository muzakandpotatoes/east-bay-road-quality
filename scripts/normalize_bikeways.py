"""Normalise every collected bikeway source into one schema.

The sources disagree about vocabulary, completeness and vintage, so nothing is
merged or deduplicated here -- each segment keeps its `source`, and the summary
measures every source against the Berkeley plan's own mileage table.  Choosing
which source to trust per class is a decision for the map, not for this step.

Facility classes follow Caltrans (as the Berkeley plan and Oakland's GIS both
do):

    I     shared-use path, physically separate from the roadway
    II    bicycle lane, striped
    IIB   upgraded/buffered bicycle lane
    III   bicycle route, shared lane, signed
    IIIA  bicycle route with shared-lane markings (sharrows)
    IIIB  bicycle boulevard, traffic-calmed shared street
    IV    separated bikeway / cycletrack, vertical separation
"""
import csv, json, os, re, sys, collections
from shapely.geometry import shape
from shapely.ops import transform as sh_transform
from shapely.strtree import STRtree
from pyproj import Transformer

to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26910", always_xy=True).transform
M_PER_MILE = 1609.344

CLASS_LABEL = {
    "I":    "Class I - Shared-use path",
    "II":   "Class II - Bike lane",
    "IIB":  "Class II - Upgraded/buffered bike lane",
    "III":  "Class III - Bike route",
    "IIIA": "Class III - Bike route with sharrows",
    "IIIB": "Class III - Bicycle boulevard",
    "IV":   "Class IV - Separated bikeway",
}
# Oakland encodes the class as a Caltrans code; a compound value such as
# "2.3A" means the two directions of a divided street differ, and the first
# token is the primary facility.
OAKLAND_CLASS = {"1": "I", "2": "II", "2B": "IIB", "3": "III",
                 "3A": "IIIA", "3B": "IIIB", "4": "IV"}


def miles(geom):
    return sh_transform(to_utm, shape(geom)).length / M_PER_MILE


def oakland_class(code):
    code = (code or "").strip().upper()
    if not code or code in ("0", "0.0", "NONE"):
        return None, None
    tokens = [t for t in code.split(".") if t and t not in ("0",)]
    if not tokens:
        return None, None
    primary = OAKLAND_CLASS.get(tokens[0])
    secondary = OAKLAND_CLASS.get(tokens[1]) if len(tokens) > 1 else None
    return primary, secondary


def osm_class(tags):
    """Map OSM cycling tags onto Caltrans classes."""
    if tags.get("highway") == "cycleway":
        return "I"
    if tags.get("bicycle_road") == "yes" or tags.get("cyclestreet") == "yes":
        return "IIIB"
    sides = [tags.get(k) for k in
             ("cycleway", "cycleway:both", "cycleway:left", "cycleway:right")]
    sides = [v for v in sides if v]
    if any(v == "track" for v in sides):
        return "IV"
    if any(v in ("lane", "opposite_lane", "buffered_lane") for v in sides):
        buffered = any("buffer" in k for k in tags) or "buffered_lane" in sides
        return "IIB" if buffered else "II"
    if any(v == "shared_lane" for v in sides):
        return "IIIA"
    return None        # cycleway=no / separate / share_busway and friends


def berkeley_plan_class(row):
    """The plan names the class in its treatment text."""
    t = (row.get("treatment") or "").lower()
    raw = (row.get("class_raw") or "").upper()
    if raw == "IV" or "cycletrack" in t:
        return "IV"
    if "boulevard" in t:
        return "IIIB"
    if raw == "II":
        return "IIB" if "upgraded" in t else "II"
    if raw in ("I", "III"):
        return raw
    return None


def title(name):
    name = " ".join((name or "").split())
    return name if not name or name[:1].isdigit() else name.title()


def load(path):
    return json.load(open(path))["features"]


def city_assigner(boundaries_path):
    feats = load(boundaries_path)
    polys = [shape(f["geometry"]) for f in feats]
    names = [f["properties"]["city"] for f in feats]
    tree = STRtree(polys)

    def assign(geom):
        pt = shape(geom).interpolate(0.5, normalized=True)
        for i in tree.query(pt):
            if polys[i].contains(pt):
                return names[i]
        return None
    return assign


def main(raw_dir, out_geojson, out_csv, out_summary):
    city_of = city_assigner(os.path.join(raw_dir, "city_boundaries.geojson"))
    feats = []

    def add(geom, city, source, status, cls, **props):
        if not cls or not geom:
            return
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            "city": city, "source": source, "status": status,
            "bikeway_class": cls, "class_label": CLASS_LABEL[cls],
            "miles": round(miles(geom), 4), **props}})

    # ---- Oakland: one layer carrying both an existing and a proposed class --
    for f in load(os.path.join(raw_dir, "oakland_bikenetwork.geojson")):
        p, g = f["properties"], f["geometry"]
        street = title(p.get("ROADWAY"))
        for field, status in (("EXISTINGCL", "existing"), ("PROPOSEDCL", "proposed")):
            cls, second = oakland_class(p.get(field))
            add(g, "Oakland", "Oakland GIS: BikeNetwork", status, cls,
                street=street, from_location=title(p.get("BEGINNING")),
                to_location=title(p.get("ENDING")),
                class_raw=(p.get(field) or "").strip(),
                class_secondary=second,
                year=(str(p.get("YEARCURREN")) if status == "existing"
                      and str(p.get("YEARCURREN") or "0") != "0" else None))

    # ---- Berkeley existing: three partial city layers ----------------------
    for f in load(os.path.join(raw_dir, "berkeley_bike_blvd.geojson")):
        p = f["properties"]
        add(f["geometry"], "Berkeley", "Berkeley GIS: Bicycle Boulevards",
            "existing", "IIIB", street=title(p.get("BB_STRNAM")),
            from_location=title(p.get("BB_FRO")), to_location=title(p.get("BB_TO")),
            class_raw="bike boulevard", class_secondary=None, year=None)

    for f in load(os.path.join(raw_dir, "berkeley_separated.geojson")):
        p = f["properties"]
        add(f["geometry"], "Berkeley", "Berkeley GIS: Separated Bikeways",
            "existing", "IV", street=title(p.get("BB_STRNAM")),
            from_location=title(p.get("BB_FRO")), to_location=title(p.get("BB_TO")),
            class_raw=(p.get("Existing_A") or "").strip(),
            class_secondary=None, year=None)

    BK_2004 = {"1": "I", "2": "II", "3": "III", "BB - 3": "IIIB", "BB -3": "IIIB"}
    for f in load(os.path.join(raw_dir, "berkeley_bikeways.geojson")):
        p = f["properties"]
        raw = (p.get("EX2004") or "").strip()
        add(f["geometry"], "Berkeley", "Berkeley GIS: Bikeways (2004 classes)",
            "existing", BK_2004.get(raw), street=title(p.get("STREET")),
            from_location=title(p.get("FROM_")), to_location=title(p.get("TO_")),
            class_raw=raw, class_secondary=None, year="2004")

    # ---- OSM: current on-the-ground tagging, both cities --------------------
    for f in load(os.path.join(raw_dir, "osm_bikeways.geojson")):
        tags = f["properties"]
        cls = osm_class(tags)
        city = city_of(f["geometry"]) if cls else None
        if city:
            add(f["geometry"], city, "OpenStreetMap", "existing", cls,
                street=title(tags.get("name")), from_location=None, to_location=None,
                class_raw=";".join(f"{k}={v}" for k, v in sorted(tags.items())
                                   if k.startswith("cycleway") or k in
                                   ("highway", "bicycle_road", "cyclestreet"))[:180],
                class_secondary=None, year=None)

    # ---- Berkeley proposed: the 2026 plan's Tier 1 list (no geometry yet) ---
    plan_rows = list(csv.DictReader(
        open(os.path.join(raw_dir, "berkeley_plan_projects.csv"))))
    plan_segments = []
    for r in plan_rows:
        if r["kind"] != "segment":
            continue
        cls = berkeley_plan_class(r)
        if cls:
            plan_segments.append({**r, "bikeway_class": cls,
                                  "class_label": CLASS_LABEL[cls]})

    json.dump({"type": "FeatureCollection", "features": feats}, open(out_geojson, "w"))

    fields = ["city", "source", "status", "bikeway_class", "class_label",
              "street", "from_location", "to_location", "miles", "class_raw",
              "class_secondary", "year"]
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for f in feats:
            w.writerow({k: f["properties"].get(k) for k in fields})

    # ---- summary, measured against the plan's own mileage table ------------
    plan_mileage = json.load(open(os.path.join(raw_dir, "berkeley_plan_mileage.json")))
    by_source = collections.defaultdict(lambda: collections.defaultdict(float))
    for f in feats:
        p = f["properties"]
        by_source[(p["city"], p["source"], p["status"])][p["bikeway_class"]] += p["miles"]

    summary = {
        "berkeley_plan_2025_mileage": plan_mileage,
        "sources": {f"{c} | {s} | {st}": {k: round(v, 1) for k, v in sorted(d.items())}
                    for (c, s, st), d in sorted(by_source.items())},
        "berkeley_plan_tier1_segments": {
            "count": len(plan_segments),
            "miles": round(sum(float(r["miles"]) for r in plan_segments), 1),
            "by_class": dict(collections.Counter(r["bikeway_class"] for r in plan_segments)),
        },
    }
    json.dump(summary, open(out_summary, "w"), indent=2)

    print(f"{len(feats)} normalised segments -> {out_geojson}, {out_csv}")
    for key, d in summary["sources"].items():
        total = sum(d.values())
        print(f"  {key:52s} {total:6.1f} mi  {dict(d)}")
    print("\nBerkeley plan Table 4 (existing 2025, authoritative):")
    for k, v in plan_mileage.items():
        print(f"    {k:38s} {v['2025']:5.1f} mi")
    print(f"\nBerkeley plan Tier 1 recommended segments: "
          f"{summary['berkeley_plan_tier1_segments']['count']} projects, "
          f"{summary['berkeley_plan_tier1_segments']['miles']} mi "
          f"{summary['berkeley_plan_tier1_segments']['by_class']}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
