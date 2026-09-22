"""Pack the joined data into a compact payload for the single-file map.

Plain GeoJSON for this dataset is ~12 MB, which is too heavy to inline.  Three
lossless-enough steps bring it under 2 MB:

  * drop collinear vertices (the REST services return densified lines) with a
    ~1 m simplify tolerance -- well under a lane width, so shapes are unchanged
    at any zoom the map offers;
  * quantise coordinates to 1e-5 degrees (~1 m) and delta-encode them as
    integers, so a vertex costs a couple of characters instead of twenty;
  * hold every repeated string (street names, cross streets, treatments) once
    in a pool and reference it by index.
"""
import json, sys
from shapely.geometry import shape, mapping

SIMPLIFY_DEG = 1e-5      # ~1.1 m
SCALE = 100_000          # 1e-5 degree grid

SECTION_KEYS = ["city", "street", "section_id", "from", "to", "pci", "rsl_years",
                "functional_class", "surface_type", "length_ft", "width_ft",
                "area_sqft", "survey_date", "last_treatment", "last_treatment_date",
                "match_method", "bike_tier"]


class Pool:
    def __init__(self):
        self.items, self.index = [], {}

    def add(self, value):
        if value is None or value == "":
            return None
        s = str(value)
        if s not in self.index:
            self.index[s] = len(self.items)
            self.items.append(s)
        return self.index[s]


def encode_geometry(geom):
    """[[x0, y0, dx, dy, ...], ...] in 1e-5 degree integer units."""
    g = shape(geom).simplify(SIMPLIFY_DEG, preserve_topology=False)
    g = mapping(g)
    parts = ([g["coordinates"]] if g["type"] == "LineString" else g["coordinates"])
    out = []
    for part in parts:
        flat, px, py = [], 0, 0
        for x, y, *_ in part:
            ix, iy = round(x * SCALE), round(y * SCALE)
            flat.extend([ix - px, iy - py])
            px, py = ix, iy
        if len(flat) >= 4:
            out.append(flat)
    return out


def pack_layer(features, keys, pool, string_keys):
    rows, geoms = [], []
    for f in features:
        g = encode_geometry(f["geometry"])
        if not g:
            continue
        p = f["properties"]
        rows.append([pool.add(p.get(k)) if k in string_keys else p.get(k) for k in keys])
        geoms.append(g)
    return {"keys": keys, "rows": rows, "geom": geoms}


def main(sections_gj, bk_plan, bk_morat, oak_plan, oak_morat, bike_offstreet,
         summary_json, out_json):
    pool = Pool()
    load = lambda p: json.load(open(p))["features"]

    payload = {"scale": SCALE, "summary": json.load(open(summary_json))}
    payload["sections"] = pack_layer(
        load(sections_gj), SECTION_KEYS, pool,
        {"city", "street", "section_id", "from", "to", "functional_class",
         "surface_type", "survey_date", "last_treatment", "last_treatment_date",
         "match_method", "bike_tier"})

    plan_keys = ["city", "street", "from", "to", "label", "detail", "year"]
    morat_keys = ["city", "street", "from", "to", "ends", "active"]

    def as_plan(feats, city, label_of, detail_of, year_of):
        out = []
        for f in feats:
            p = f["properties"]
            out.append({"geometry": f["geometry"], "properties": {
                "city": city, "street": p.get("street"), "from": p.get("from"),
                "to": p.get("to"), "label": label_of(p), "detail": detail_of(p),
                "year": year_of(p)}})
        return out

    plan_feats = (
        as_plan(load(bk_plan), "Berkeley",
                lambda p: p.get("plan"),
                lambda p: f'{p.get("treatment")} - {p.get("cost")}',
                lambda p: p.get("fiscal_year")) +
        as_plan(load(oak_plan), "Oakland",
                lambda p: p.get("plan"),
                lambda p: p.get("category"),
                lambda p: None))
    payload["plan"] = pack_layer(plan_feats, plan_keys, pool,
                                 {"city", "street", "from", "to", "label", "detail"})

    def as_morat(feats, city, ends_of, from_of, to_of):
        return [{"geometry": f["geometry"], "properties": {
            "city": city, "street": f["properties"].get("street"),
            "from": from_of(f["properties"]), "to": to_of(f["properties"]),
            "ends": ends_of(f["properties"]),
            "active": 1 if f["properties"].get("active") else 0}} for f in feats]

    morat_feats = (
        as_morat(load(bk_morat), "Berkeley", lambda p: p.get("ends"),
                 lambda p: p.get("from"), lambda p: p.get("to")) +
        as_morat(load(oak_morat), "Oakland", lambda p: p.get("ends"),
                 lambda p: p.get("project"), lambda p: p.get("program")))
    payload["moratorium"] = pack_layer(morat_feats, morat_keys, pool,
                                       {"city", "street", "from", "to", "ends"})

    # Only off-street paths need their own geometry: on-street bikeways are
    # drawn from the PCI sections' own lines via their `bike_tier`.
    payload["offstreet"] = pack_layer(json.load(open(bike_offstreet))["features"],
                                      ["city", "tier", "tier_label", "street", "miles"],
                                      pool, {"city", "tier", "tier_label", "street"})

    payload["pool"] = pool.items
    text = json.dumps(payload, separators=(",", ":"))
    open(out_json, "w").write(text)
    for name in ("sections", "plan", "moratorium", "offstreet"):
        print(f"  {name}: {len(payload[name]['rows'])} features")
    print(f"  string pool: {len(pool.items)} entries")
    print(f"packed {len(text)/1e6:.2f} MB -> {out_json}")


if __name__ == "__main__":
    main(*sys.argv[1:9])
