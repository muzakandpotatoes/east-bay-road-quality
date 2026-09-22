"""How much of Berkeley's built bikeway network do the sources recover together?

No source matches the Berkeley Bicycle Plan's Table 4 alone.  This measures
what a merge would actually buy, by adding sources one at a time and counting
only the length each one contributes that is not already covered within
TOL metres of the geometry accepted so far.  Coverage is measured two ways:

  per class   -- does the merge reach the plan's mileage for that class?
  any class   -- total centreline miles carrying any bikeway, against the
                 plan's 60.9 mi total; this is the number that matters, since
                 a street the sources disagree about the class of must not be
                 counted twice.
"""
import json, sys, collections
from shapely.geometry import shape
from shapely.ops import transform as sh_transform, unary_union
from pyproj import Transformer

to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26910", always_xy=True).transform
M_PER_MILE = 1609.344
TOL = 15.0          # metres; two sources within this are the same street

# Most trusted first: the city's own layers for what they cover, then OSM.
ORDER = ["Berkeley GIS: Separated Bikeways", "Berkeley GIS: Bicycle Boulevards",
         "OpenStreetMap", "Berkeley GIS: Bikeways (2004 classes)"]
PLAN_KEY = {"I": "Shared use path (Class I)", "II": "Bicycle lane (Class II)",
            "IIB": "Upgraded bicycle lane (Class II)", "III": "Bicycle route (Class III)",
            "IIIA": "Bicycle route (Class III)", "IIIB": "Bicycle boulevard (Class III)",
            "IV": "Separated bikeway (Class IV)"}


def utm(feat):
    return sh_transform(to_utm, shape(feat["geometry"]))


def incremental(groups):
    """[(source, lines)] in priority order -> (rows, merged_miles)."""
    accepted, rows = None, []
    for source, lines in groups:
        geom = unary_union(lines)
        alone = geom.length / M_PER_MILE
        if accepted is None:
            new = geom
        else:
            new = geom.difference(accepted.buffer(TOL))
        added = new.length / M_PER_MILE
        accepted = geom if accepted is None else unary_union([accepted, new])
        rows.append((source, alone, added, accepted.length / M_PER_MILE))
    return rows, (accepted.length / M_PER_MILE if accepted is not None else 0.0)


def main(bikeways_geojson, plan_mileage_json):
    feats = [f for f in json.load(open(bikeways_geojson))["features"]
             if f["properties"]["city"] == "Berkeley"
             and f["properties"]["status"] == "existing"]
    plan = json.load(open(plan_mileage_json))

    by_class = collections.defaultdict(lambda: collections.defaultdict(list))
    everything = collections.defaultdict(list)
    for f in feats:
        p = f["properties"]
        g = utm(f)
        by_class[p["bikeway_class"]][p["source"]].append(g)
        everything[p["source"]].append(g)

    print(f"Berkeley existing bikeways, merge tolerance {TOL:.0f} m\n")
    print(f"{'class':5s} {'source':38s} {'alone':>7s} {'adds':>7s} {'merged':>7s}  plan")
    print("-" * 78)
    for cls in ("I", "II", "IIB", "III", "IIIA", "IIIB", "IV"):
        if cls not in by_class:
            continue
        groups = [(s, by_class[cls][s]) for s in ORDER if s in by_class[cls]]
        rows, merged = incremental(groups)
        target = plan.get(PLAN_KEY[cls], {}).get("2025")
        for i, (source, alone, added, running) in enumerate(rows):
            tgt = f"{target:.1f}" if (i == 0 and target is not None) else ""
            print(f"{cls if i==0 else '':5s} {source:38s} {alone:7.1f} {added:7.1f} "
                  f"{running:7.1f}  {tgt}")
        if target:
            print(f"{'':5s} {'-> merged vs plan':38s} {'':7s} {'':7s} {merged:7.1f}  "
                  f"{merged/target*100:.0f}% of {target:.1f}")
        print()

    groups = [(s, everything[s]) for s in ORDER if s in everything]
    rows, merged = incremental(groups)
    total = plan["Total"]["2025"]
    print("ANY CLASS (a street counted once, whatever class the sources call it)")
    for source, alone, added, running in rows:
        print(f"      {source:38s} {alone:7.1f} {added:7.1f} {running:7.1f}")
    print(f"      {'-> merged vs plan total':38s} {'':7s} {'':7s} {merged:7.1f}  "
          f"{merged/total*100:.0f}% of {total:.1f}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
