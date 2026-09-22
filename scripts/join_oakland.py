"""Join Oakland's PCI table to its pavement-section geometry.

Oakland's GIS publishes the StreetSaver section geometry itself
(Paving_PCI_2024Values), carrying the same STREETID/SECTIONID keys the report
prints, so this is an exact key join rather than the cross-street matching
Berkeley needs.  The layer also has its own PCI_2024 column, which is kept
only as a cross-check -- the mapped value always comes from the report.
"""
import csv, json, sys, collections
from shapely.geometry import shape
from shapely.ops import transform as sh_transform
from pyproj import Transformer

FT_PER_M = 3.280839895
to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26910", always_xy=True).transform

# The report prints PCI as of this date; field survey ran Sept-Dec 2024.
SURVEY_DATE = "01/15/2025"


def key(street_id, section_id):
    return f"{str(street_id).lstrip('0').upper()}-{str(section_id).lstrip('0').upper()}"


def main(pci_csv, sections_geojson, out_geojson, report_json):
    rows = list(csv.DictReader(open(pci_csv)))
    fc = json.load(open(sections_geojson))

    geo = {}
    for feat in fc["features"]:
        p = feat["properties"]
        geo.setdefault(key(p["STREETID"], p["SECTIONID"]), []).append(feat)

    out, unmatched, pci_delta = [], [], collections.Counter()
    for row in rows:
        k = key(row["street_id"], row["section_id"])
        if k not in geo:
            unmatched.append({"street_name": row["street_name"], "section_id": row["section_id"],
                              "from": row["from_location"], "to": row["to_location"],
                              "reason": "no geometry with this street/section id"})
            continue
        feat = geo[k][0]
        gis_pci = feat["properties"].get("PCI_2024")
        if gis_pci is not None:
            pci_delta[int(row["pci"]) - int(gis_pci)] += 1

        geom = feat["geometry"]
        matched_ft = sh_transform(to_utm, shape(geom)).length * FT_PER_M
        reported_ft = float(row["length_ft"]) if row["length_ft"] else None
        err = abs(matched_ft - reported_ft) / reported_ft if reported_ft else None

        out.append({"type": "Feature", "geometry": geom, "properties": {
            "city": "Oakland",
            "street": row["street_name"],
            "section_id": f'{row["street_id"]}-{row["section_id"]}',
            "from": row["from_location"],
            "to": row["to_location"],
            "pci": int(row["pci"]),
            "rsl_years": float(row["rsl_years"]) if row["rsl_years"] else None,
            "functional_class": row["functional_class"],
            "surface_type": row["surface_type"],
            "length_ft": reported_ft,
            "width_ft": float(row["width_ft"]) if row["width_ft"] else None,
            "area_sqft": float(row["area_sqft"]) if row["area_sqft"] else None,
            "survey_date": SURVEY_DATE,
            "last_treatment": None,
            "last_treatment_date": None,
            "match_length_err": round(err, 3) if err is not None else None,
            "match_method": "section id",
        }})

    matched = len(out)
    print(f"matched {matched}/{len(rows)} ({100*matched/len(rows):.1f}%), "
          f"unmatched {len(rows)-matched} ({100*(len(rows)-matched)/len(rows):.1f}%)")
    agree = sum(n for d, n in pci_delta.items() if abs(d) <= 1)
    total = sum(pci_delta.values())
    print(f"cross-check vs the layer's own PCI_2024: {agree}/{total} "
          f"({100*agree/total:.0f}%) within 1 point")

    json.dump({"type": "FeatureCollection", "features": out}, open(out_geojson, "w"))
    json.dump({"city": "Oakland", "rows": len(rows), "matched": matched,
               "unmatched_rate": round(1 - matched / len(rows), 4),
               "reasons": {"no geometry with this street/section id": len(unmatched)},
               "pci_vs_gis_layer": {str(k): v for k, v in sorted(pci_delta.items())},
               "unmatched": unmatched}, open(report_json, "w"), indent=2)
    print(f"wrote {out_geojson} and {report_json}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
