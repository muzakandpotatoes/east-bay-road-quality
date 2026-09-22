"""Extract the bikeway tables from the Berkeley Bicycle Plan 2026.

Two things come out:

  * Table 11, the Tier 1 project list -- corridor, recommended treatment,
    location and the two cross streets, so recommended bikeways can be placed
    on the centerline network the same way PCI sections are.
  * Table 4, existing network mileage by Caltrans class for 2017 and 2025,
    which is the only authoritative statement of what Berkeley has actually
    built.  It is the yardstick the collected layers are measured against.

The plan's network maps (Figures 5 and 13) are raster figures, so segment-level
*existing* geometry is not in the document -- that still has to come from GIS.
"""
import csv, json, re, sys
import pdfplumber
from pdf_table import group_lines, make_column_cutter

CLASS_RE = re.compile(r"Class\s+(IV|III|II|I)\b", re.I)


def cell(t):
    return " ".join((t or "").split())


def extract_mileage(pdf):
    """Table 4: facility type -> {2017, 2025} miles."""
    for page in pdf.pages:
        text = page.extract_text() or ""
        if "Bikeway Network Mileage Comparison" not in text:
            continue
        rows = {}
        for line in text.split("\n"):
            m = re.match(r"(.+?\(Class\s+(?:IV|III|II|I)\))\s+([\d.]+)\s+([\d.]+)\*?\s*$", line.strip())
            if m:
                rows[cell(m.group(1))] = {"2017": float(m.group(2)), "2025": float(m.group(3))}
            elif re.match(r"^Total\s+[\d.]+\s+[\d.]+\s*$", line.strip()):
                a, b = line.split()[1:3]
                rows["Total"] = {"2017": float(a), "2025": float(b)}
        if rows:
            return rows
    return {}


COLS = ["corridor", "treatment", "location", "cross_a", "cross_b", "miles", "cost"]
NOISE = re.compile(r"^(Table 11|RECOMMENDED|TOTAL COST|CORRIDOR|ESTIMATE|STUDY|"
                   r"Implementation \||PROJECT OR)")


def extract_projects(pdf):
    """Table 11, the Tier 1 project list.

    Only the header row is ruled, so the header's own cell edges are reused to
    cut the unruled data lines.  Every field in a row can wrap onto the lines
    above or below it, and the corridor column is a merged cell drawn beside a
    whole group of rows -- so the cost column anchors the parse.  Exactly one
    cost is printed per project, which makes cost lines the row skeleton;
    every other line is a fragment that joins its nearest row.
    """
    out = []
    for page in pdf.pages:
        if "Tier 1 Project List" not in (page.extract_text() or ""):
            continue
        tables = page.find_tables()
        if not tables:
            continue
        edges = [c[2] for c in tables[0].rows[0].cells if c]
        if len(edges) < len(COLS):
            continue
        cut = make_column_cutter(list(zip(COLS, edges[:-1] + [10_000])))

        anchors, frags, labels = [], [], []
        for words in group_lines(page.extract_words()):
            row = cut(words)
            joined = " ".join(v for v in row.values() if v)
            if not joined or NOISE.match(joined):
                continue
            y = min(w["top"] for w in words)
            if re.search(r"\$\s*[\d,]+", row["cost"] or ""):
                anchors.append((y, row))
            elif [k for k, v in row.items() if v] == ["corridor"]:
                labels.append((y, row["corridor"]))
            else:
                frags.append((y, row))
        if not anchors:
            continue
        anchors.sort()

        # Merged corridor cells: stitch adjacent fragments into one label.
        groups = []
        for y, text in sorted(labels):
            if groups and y - groups[-1][1] < 16:
                groups[-1] = (groups[-1][0] + " " + text, y)
            else:
                groups.append((text, y))

        parts = {i: [] for i in range(len(anchors))}
        for y, row in frags:
            i = min(range(len(anchors)), key=lambda j: abs(anchors[j][0] - y))
            parts[i].append((y, row))

        for i, (y, row) in enumerate(anchors):
            for key in ("treatment", "location", "cross_a", "cross_b"):
                pieces = [(fy, f[key]) for fy, f in parts[i] if f[key]]
                if row[key]:
                    pieces.append((y, row[key]))
                row[key] = " ".join(t for _, t in sorted(pieces))
            if not row["corridor"] and groups:
                row["corridor"] = min(groups, key=lambda g: abs(g[1] - y))[0]
            miles = row["miles"] or next(
                (f["miles"] for _, f in parts[i] if f["miles"]), "")
            row["miles"] = float(miles) if re.fullmatch(r"[\d.]+", miles or "") else None
            cls = CLASS_RE.search(row["treatment"])
            row["class_raw"] = cls.group(1).upper() if cls else ""
            # A mileage marks a linear bikeway; spot treatments (traffic
            # circles, beacons, diverters) carry a location and no length.
            row["kind"] = "segment" if row["miles"] else "spot"
            row["cost"] = re.sub(r"[^\d]", "", row["cost"])
            out.append(row)
    return out


def main(pdf_path, projects_csv, mileage_json):
    with pdfplumber.open(pdf_path) as pdf:
        mileage = extract_mileage(pdf)
        projects = extract_projects(pdf)

    if not mileage:
        raise SystemExit("could not find Table 4 (network mileage)")
    total = mileage.get("Total", {}).get("2025")
    summed = round(sum(v["2025"] for k, v in mileage.items() if k != "Total"), 1)
    print("existing network mileage by class (Table 4):")
    for k, v in mileage.items():
        print(f"    {k:38s} 2017 {v['2017']:5.1f}   2025 {v['2025']:5.1f}")
    if total is not None and abs(summed - total) > 0.15:
        raise SystemExit(f"class mileages sum to {summed}, table says {total}")
    print(f"    class rows sum to {summed} vs stated total {total}  OK")

    with open(mileage_json, "w") as fh:
        json.dump(mileage, fh, indent=2)
    with open(projects_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS + ["class_raw", "kind"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(projects)
    # Control total: the plan states Tier 1 capital cost in Table 13.
    EXPECT_ROWS, EXPECT_COST = 90, 14_276_500
    cost = sum(int(p["cost"]) for p in projects if p["cost"])
    ok = len(projects) == EXPECT_ROWS and cost == EXPECT_COST
    print(f"\n{'OK ' if ok else 'BAD'} Tier 1 rows {len(projects)} (plan: {EXPECT_ROWS}), "
          f"cost ${cost:,} (plan: ${EXPECT_COST:,})")
    if not ok:
        raise SystemExit("Tier 1 project list disagrees with the plan's own total")
    seg = [p for p in projects if p["kind"] == "segment"]
    spot = [p for p in projects if p["kind"] == "spot"]
    print(f"\nTier 1 project list: {len(projects)} rows")
    print(f"    {len(seg):3d} linear bikeway projects, {sum(p['miles'] for p in seg):.1f} mi")
    print(f"    {len(spot):3d} spot treatments (crossings, diverters, traffic circles)")
    import collections
    print("    by class:", dict(collections.Counter(p["class_raw"] or "-" for p in seg)))
    print(f"wrote {projects_csv} and {mileage_json}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
