"""Join a city's PCI table to street centerline geometry.

Used for the cities whose PCI comes from a PDF listing keyed by street name
plus cross streets (Berkeley, Albany) rather than by section id (Oakland).
The matching itself lives in linref.py, which the paving-plan and moratorium
overlays reuse.

    join_city.py <city> <pci.csv> <centerlines.geojson> <out.geojson> <report.json>
                 [osm_streets.geojson] [municipality]

`municipality` filters the centerline layer to one city's own streets, which
matters because the layer covers neighbouring cities too and street names
repeat across them (Washington Ave exists in both Albany and Berkeley).
"""
import csv, json, sys, collections
from shapely.geometry import LineString
from shapely.ops import unary_union
from linref import Network, match_row, to_wgs, load_centerlines
from streetnames import normalize


def main(city, pci_csv, centerlines, out_geojson, report_json,
         osm_streets=None, municipality=None):
    rows = list(csv.DictReader(open(pci_csv)))
    feats, extra = load_centerlines(centerlines, osm_streets, municipality)
    net = Network(feats, "FULLNAME")
    print(f"network: {len(net.edges)} edges, {len(net.coords)} nodes, "
          f"{len(net.by_name)} street names ({extra} names added from OSM)")

    # Pass 1 places every section it can on cross streets alone.  Pass 2
    # retries the rest knowing where pass 1 landed: a section with only one
    # firm cross street (the other end being a rail crossing, a park, a city
    # limit) is ambiguous on its own, but the neighbouring sections of the
    # same street have already claimed one side of that intersection.
    placed, results = collections.defaultdict(list), {}
    for row in rows:
        coords, diag = match_row(net, row["street_name"], row["beg_location"],
                                 row["end_location"], row["length_ft"])
        results[id(row)] = (coords, diag)
        if coords is not None:
            placed[normalize(row["street_name"])].append(LineString(coords))

    occupied = {k: unary_union(v) for k, v in placed.items()}
    retried = 0
    for row in rows:
        coords, diag = results[id(row)]
        if coords is not None:
            continue
        coords, diag2 = match_row(net, row["street_name"], row["beg_location"],
                                  row["end_location"], row["length_ft"],
                                  occupied=occupied.get(normalize(row["street_name"])))
        if coords is not None:
            retried += 1
            results[id(row)] = (coords, diag2)
    print(f"pass 2 placed {retried} more sections using neighbouring blocks")

    out, reasons, unmatched = [], collections.Counter(), []
    for row in rows:
        coords, diag = results[id(row)]
        reasons[diag["reason"].split(" (")[0]] += 1
        if coords is None:
            unmatched.append({**{k: row.get(k) for k in
                              ("street_name", "section_id", "beg_location",
                               "end_location", "length_ft")}, **diag})
            continue
        out.append({"type": "Feature",
                    "geometry": {"type": "LineString",
                                 "coordinates": [list(to_wgs(x, y)) for x, y in coords]},
                    "properties": {
                        "city": city,
                        "street": row["street_name"],
                        "section_id": row["section_id"],
                        "from": row["beg_location"],
                        "to": row["end_location"],
                        "pci": int(row["pci"]),
                        "rsl_years": None,
                        "functional_class": row.get("fc"),
                        "surface_type": row.get("surface_type") or None,
                        "length_ft": float(row["length_ft"]) if row.get("length_ft") else None,
                        "width_ft": float(row["width_ft"]) if row.get("width_ft") else None,
                        "area_sqft": float(row["area_sqft"]) if row.get("area_sqft") else None,
                        "survey_date": row.get("pci_date") or None,
                        "last_treatment": row.get("last_mnr_treatment") or None,
                        "last_treatment_date": row.get("last_mnr_date") or None,
                        "match_length_err": diag["length_err"],
                        "match_method": diag["method"],
                    }})

    matched = len(out)
    print(f"\nmatched {matched}/{len(rows)} ({100*matched/len(rows):.1f}%), "
          f"unmatched {len(rows)-matched} ({100*(len(rows)-matched)/len(rows):.1f}%)")
    for reason, n in reasons.most_common():
        print(f"   {n:5d}  {reason}")

    json.dump({"type": "FeatureCollection", "features": out}, open(out_geojson, "w"))
    json.dump({"city": city, "rows": len(rows), "matched": matched,
               "unmatched_rate": round(1 - matched / len(rows), 4),
               "reasons": dict(reasons), "unmatched": unmatched},
              open(report_json, "w"), indent=2)
    print(f"wrote {out_geojson} and {report_json}")


if __name__ == "__main__":
    main(*sys.argv[1:8])
