"""Combine both cities' joined PCI segments into the final map dataset.

Writes GeoJSON (what the map loads) and CSV (the same rows, geometry as WKT),
plus a summary the map renders in its legend and caveats.
"""
import csv, json, sys, collections

# MTC / StreetSaver condition categories.
MTC_BREAKS = [
    (80, 100, "Very Good / Excellent"),
    (70, 79, "Good"),
    (60, 69, "Fair"),
    (50, 59, "At Risk"),
    (25, 49, "Poor"),
    (0, 24, "Failed"),
]
FIELDS = ["city", "street", "section_id", "from", "to", "pci", "mtc_category",
          "rsl_years", "functional_class", "surface_type", "length_ft", "width_ft",
          "area_sqft", "survey_date", "last_treatment", "last_treatment_date",
          "match_method", "match_length_err"]


def category(pci):
    for lo, hi, name in MTC_BREAKS:
        if lo <= pci <= hi:
            return name
    return None


def wkt(geom):
    def ring(cs):
        return ", ".join(f"{x:.6f} {y:.6f}" for x, y, *_ in cs)
    if geom["type"] == "LineString":
        return f"LINESTRING ({ring(geom['coordinates'])})"
    return "MULTILINESTRING (" + ", ".join(f"({ring(c)})" for c in geom["coordinates"]) + ")"


def main(berkeley_gj, oakland_gj, bk_report, oak_report, out_gj, out_csv, out_summary):
    feats = []
    for path in (berkeley_gj, oakland_gj):
        feats.extend(json.load(open(path))["features"])
    for f in feats:
        f["properties"]["mtc_category"] = category(f["properties"]["pci"])

    json.dump({"type": "FeatureCollection", "features": feats}, open(out_gj, "w"))

    with open(out_csv, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=FIELDS + ["geometry_wkt"])
        wr.writeheader()
        for f in feats:
            row = {k: f["properties"].get(k) for k in FIELDS}
            row["geometry_wkt"] = wkt(f["geometry"])
            wr.writerow(row)

    summary = {"cities": {}, "mtc_breaks": [[lo, hi, n] for lo, hi, n in MTC_BREAKS]}
    for path, city in ((bk_report, "Berkeley"), (oak_report, "Oakland")):
        rep = json.load(open(path))
        sub = [f["properties"] for f in feats if f["properties"]["city"] == city]
        area = sum(p["area_sqft"] or 0 for p in sub)
        summary["cities"][city] = {
            "sections_in_report": rep["rows"],
            "sections_mapped": rep["matched"],
            "unmatched_rate": rep["unmatched_rate"],
            "weighted_pci_mapped": round(
                sum(p["pci"] * (p["area_sqft"] or 0) for p in sub) / area, 1),
            "by_category": dict(collections.Counter(p["mtc_category"] for p in sub)),
        }
    json.dump(summary, open(out_summary, "w"), indent=2)

    print(f"{len(feats)} mapped sections -> {out_gj}, {out_csv}")
    for city, s in summary["cities"].items():
        print(f"  {city}: {s['sections_mapped']}/{s['sections_in_report']} mapped "
              f"({100*(1-s['unmatched_rate']):.1f}%), weighted PCI {s['weighted_pci_mapped']}")
        for lo, hi, name in MTC_BREAKS:
            print(f"      {name:22s} {s['by_category'].get(name, 0):5d}")


if __name__ == "__main__":
    main(*sys.argv[1:8])
