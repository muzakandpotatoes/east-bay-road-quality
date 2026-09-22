"""Build the bike-network layer the map draws, and tag PCI sections with it.

The map draws the casing from the *PCI section geometry itself* wherever a
bikeway runs on a mapped street, so the casing sits exactly under the coloured
line.  An earlier version drew a merged union of the source geometries instead:
because the sources trace the same street a few metres apart, the leftover
fragments rendered as a string of blobs.

That leaves off-street paths -- the Bay Trail, the Ohlone Greenway, park paths
-- which are not city streets and so have no PCI section.  Those are carried
separately here, kept as whole features rather than union fragments so they
draw as continuous lines.

Outputs:
  (in place)                every PCI section gains `bike_tier`
  bike_offstreet.geojson    bikeways with no PCI section under them
"""
import json, sys, collections
from shapely.geometry import shape, mapping
from shapely.ops import transform as sh_transform, unary_union
from shapely.strtree import STRtree
from pyproj import Transformer

to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26910", always_xy=True).transform
M_PER_MILE = 1609.344

TIER_OF = {"I": "protected", "IV": "protected",
           "II": "painted", "IIB": "painted",
           "III": "shared", "IIIA": "shared", "IIIB": "shared"}
TIERS = ["protected", "painted", "shared"]          # best first
TIER_LABEL = {"protected": "Protected / off-street path",
              "painted": "Painted bike lane",
              "shared": "Shared roadway / bike boulevard"}
SOURCE_ORDER = ["Oakland GIS: BikeNetwork",
                "Berkeley GIS: Separated Bikeways",
                "Berkeley GIS: Bicycle Boulevards",
                "OpenStreetMap",
                "Berkeley GIS: Bikeways (2004 classes)"]
ON_ROUTE_M = 12.0   # a PCI section this close to a bikeway carries it
COVERAGE = 0.5      # ...for at least half its length
DEDUP_M = 15.0      # two sources within this describe the same path
DEDUP_FRAC = 0.6    # a path this covered by what is already kept is a duplicate
MIN_MILES = 0.02    # drop slivers


def utm(geom):
    return sh_transform(to_utm, shape(geom))


def main(pci_path, bikeways_path, out_offstreet, out_summary):
    bike = [f for f in json.load(open(bikeways_path))["features"]
            if f["properties"]["status"] == "existing"]
    # Only cities that actually have bikeway data; others simply get no tiers.
    CITIES = sorted({f["properties"]["city"] for f in bike})
    for f in bike:
        f["_g"] = utm(f["geometry"])
        f["_tier"] = TIER_OF.get(f["properties"]["bikeway_class"])

    pci = json.load(open(pci_path))
    for f in pci["features"]:
        f["_g"] = utm(f["geometry"])

    # ---- tag PCI sections, best tier first ---------------------------------
    for city in CITIES:
        for tier in TIERS:
            lines = [f["_g"] for f in bike
                     if f["properties"]["city"] == city and f["_tier"] == tier]
            if not lines:
                continue
            buf = unary_union(lines).buffer(ON_ROUTE_M)
            for f in pci["features"]:
                if f["properties"]["city"] != city or f["properties"].get("bike_tier"):
                    continue
                if f["_g"].intersection(buf).length > COVERAGE * f["_g"].length:
                    f["properties"]["bike_tier"] = tier
    for f in pci["features"]:
        f["properties"].setdefault("bike_tier", None)

    # ---- off-street residual: bikeways no tagged section accounts for ------
    tagged = {c: [f["_g"] for f in pci["features"]
                  if f["properties"]["city"] == c and f["properties"]["bike_tier"]]
              for c in CITIES}
    offstreet = []
    for city in CITIES:
        street_buf = (unary_union(tagged[city]).buffer(ON_ROUTE_M)
                      if tagged[city] else None)
        kept = []
        for tier in TIERS:
            for source in SOURCE_ORDER:
                for f in bike:
                    if (f["properties"]["city"] != city or f["_tier"] != tier
                            or f["properties"]["source"] != source):
                        continue
                    g = f["_g"]
                    if g.length / M_PER_MILE < MIN_MILES:
                        continue
                    if street_buf is not None and \
                            g.intersection(street_buf).length > DEDUP_FRAC * g.length:
                        continue          # already drawn as a street casing
                    if kept:
                        tree = STRtree([k for k, _ in kept])
                        near = [kept[i][0] for i in tree.query(g.buffer(DEDUP_M))]
                        if near and g.intersection(unary_union(near).buffer(DEDUP_M)
                                                   ).length > DEDUP_FRAC * g.length:
                            continue      # another source already has this path
                    kept.append((g, tier))
                    offstreet.append({"type": "Feature", "geometry": f["geometry"],
                                      "properties": {
                                          "city": city, "tier": tier,
                                          "tier_label": TIER_LABEL[tier],
                                          "street": f["properties"].get("street"),
                                          "miles": round(g.length / M_PER_MILE, 3)}})
    json.dump({"type": "FeatureCollection", "features": offstreet},
              open(out_offstreet, "w"))

    for f in pci["features"]:
        del f["_g"]
    json.dump(pci, open(pci_path, "w"))

    # ---- summary ----------------------------------------------------------
    summary = json.load(open(out_summary))
    for city in CITIES:
        rows = [f for f in pci["features"] if f["properties"]["city"] == city]
        on = [f for f in rows if f["properties"]["bike_tier"]]

        def wpci(fs):
            a = sum(f["properties"]["area_sqft"] or 0 for f in fs)
            return (round(sum(f["properties"]["pci"] * (f["properties"]["area_sqft"] or 0)
                              for f in fs) / a, 1) if a else None)

        street_mi = collections.Counter()
        for f in on:
            street_mi[f["properties"]["bike_tier"]] += (f["properties"]["length_ft"] or 0) / 5280
        off_mi = collections.Counter()
        for f in offstreet:
            if f["properties"]["city"] == city:
                off_mi[f["properties"]["tier"]] += f["properties"]["miles"]

        poor = sum(1 for f in on if f["properties"]["mtc_category"] in ("Poor", "Failed"))
        summary["cities"][city]["bike_network"] = {
            "sections_on_network": len(on),
            "weighted_pci_on_network": wpci(on),
            "weighted_pci_citywide": wpci(rows),
            "poor_or_failed_on_network": poor,
            "miles_by_tier": {t: round(street_mi[t] + off_mi[t], 1) for t in TIERS},
            "on_street_miles": {t: round(street_mi[t], 1) for t in TIERS},
            "off_street_miles": {t: round(off_mi[t], 1) for t in TIERS},
        }
        b = summary["cities"][city]["bike_network"]
        print(f"{city}: {len(on)} sections carry a bikeway "
              f"(PCI {b['weighted_pci_on_network']} vs {b['weighted_pci_citywide']} citywide, "
              f"{poor} Poor/Failed)")
        print(f"    on-street  {b['on_street_miles']}")
        print(f"    off-street {b['off_street_miles']}  "
              f"({sum(1 for f in offstreet if f['properties']['city']==city)} paths)")
    json.dump(summary, open(out_summary, "w"), indent=2)
    print(f"wrote {out_offstreet} ({len(offstreet)} features)")


if __name__ == "__main__":
    main(*sys.argv[1:5])
