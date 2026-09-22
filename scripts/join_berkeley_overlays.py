"""Place Berkeley's paving plans and moratorium streets on the centerlines.

Both lists are keyed the same way as the PCI appendix -- street plus from/to
cross streets -- so they go through the same matcher.  The plan prints a
mileage per project, which is used both to disambiguate and to gate the
match; the moratorium list prints none, so those fall back to the shortest
route between the two cross streets.
"""
import csv, json, sys, collections, datetime
from linref import Network, match_row, to_wgs, load_centerlines

TODAY = datetime.date(2026, 9, 19)


def feature(coords, props):
    return {"type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [list(to_wgs(x, y)) for x, y in coords]},
            "properties": props}


def run(net, rows, build_props, length_of, label):
    out, reasons, unmatched = [], collections.Counter(), []
    for row in rows:
        coords, diag = match_row(net, row["street_name"], row["beg_location"],
                                 row["end_location"], length_of(row))
        reasons[diag["reason"].split(" (")[0]] += 1
        if coords is None:
            unmatched.append({**row, **diag})
            continue
        out.append(feature(coords, build_props(row, diag)))
    print(f"{label}: matched {len(out)}/{len(rows)} "
          f"({100*len(out)/len(rows):.1f}%)")
    for reason, n in reasons.most_common():
        print(f"     {n:4d}  {reason}")
    return out, unmatched


def main(plan_csv, morat_csv, centerlines, plan_geojson, morat_geojson, report_json,
         osm_streets=None):
    feats, extra = load_centerlines(centerlines, osm_streets)
    net = Network(feats, "FULLNAME")
    print(f"network: {len(net.edges)} edges, {len(net.by_name)} street names "
          f"({extra} from OSM)\n")

    plan_rows = list(csv.DictReader(open(plan_csv)))
    plan_feats, plan_un = run(
        net, plan_rows,
        lambda r, d: {"layer": "paving_plan", "plan": r["plan"],
                      "fiscal_year": int(r["fiscal_year"]), "street": r["street_name"],
                      "from": r["beg_location"], "to": r["end_location"],
                      "pci": int(r["pci"]) if r["pci"] else None,
                      "miles": float(r["miles"]) if r["miles"] else None,
                      "treatment": r["treatment"], "cost": r["cost"]},
        lambda r: float(r["miles"]) * 5280 if r["miles"] else None,
        "paving plan")

    morat_rows = list(csv.DictReader(open(morat_csv)))
    def morat_props(r, d):
        ends = datetime.datetime.strptime(r["ends"], "%m/%d/%Y").date()
        return {"layer": "moratorium", "street": r["street_name"],
                "from": r["beg_location"], "to": r["end_location"],
                "begins": r["begins"], "ends": r["ends"],
                "active": ends >= TODAY}
    morat_feats, morat_un = run(net, morat_rows, morat_props, lambda r: None, "moratorium")
    active = sum(1 for f in morat_feats if f["properties"]["active"])
    print(f"     {active} of {len(morat_feats)} matched moratoriums still active on {TODAY}")

    json.dump({"type": "FeatureCollection", "features": plan_feats}, open(plan_geojson, "w"))
    json.dump({"type": "FeatureCollection", "features": morat_feats}, open(morat_geojson, "w"))
    json.dump({"paving_plan": {"rows": len(plan_rows), "matched": len(plan_feats),
                               "unmatched": plan_un},
               "moratorium": {"rows": len(morat_rows), "matched": len(morat_feats),
                              "active_matched": active, "unmatched": morat_un}},
              open(report_json, "w"), indent=2)
    print(f"\nwrote {plan_geojson}, {morat_geojson}, {report_json}")


if __name__ == "__main__":
    main(*sys.argv[1:8])
