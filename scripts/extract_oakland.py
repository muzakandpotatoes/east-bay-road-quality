"""Extract segment-level PCI/RSL rows from Oakland's P-TAP Round 25 report.

Appendix C, "Section PCI/RSL Listing" (pages 31-142).  Note this appendix
covers the whole managed network (3,971 sections), not just the 3,304 sections
physically surveyed in the 2024 field work.
"""
import csv, re, sys
import pdfplumber
from pdf_table import group_lines, make_column_cutter

COLUMNS = [
    ("street_id",         58), ("section_id",       107),
    ("street_name",      200), ("from_location",    300),
    ("to_location",      415), ("length_ft",        465),
    ("width_ft",         508), ("area_sqft",        545),
    ("functional_class", 630), ("surface_type",     700),
    ("pci",              740), ("rsl_years",     10_000),
]
MARKER = "Section PCI/RSL Listing"
NOISE = re.compile(r"^(CITY OF|Street ID|Criteria:|Printed:|Current|Remaining|Total|Section PCI)")
CONTINUABLE = ("street_name", "from_location", "to_location")

cut = make_column_cutter(COLUMNS)


def rows_from_page(page):
    rows = []
    for words in group_lines(page.extract_words()):
        row = cut(words)
        joined = " ".join(v for v in row.values() if v)
        if not joined or NOISE.match(joined):
            continue
        if re.fullmatch(r"\d{1,3}", row["pci"]) and re.fullmatch(r"[A-Z0-9]{3,8}", row["street_id"]):
            rows.append(row)
        elif rows and not row["street_id"] and not row["pci"]:
            for k in CONTINUABLE:
                if row[k]:
                    rows[-1][k] = (rows[-1][k] + " " + row[k]).strip()
    return rows


def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def main(pdf_path, out_csv):
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            if MARKER in (page.extract_text() or ""):
                out.extend(rows_from_page(page))

    for r in out:
        r["pci"] = int(r["pci"])
        for k in ("length_ft", "width_ft", "area_sqft", "rsl_years"):
            r[k] = num(r[k])
        for k in ("functional_class", "surface_type"):   # "A - Arterial" -> "Arterial"
            r[k] = r[k].split("-", 1)[-1].strip()

    # Control totals printed by the report itself: the totals row on the last
    # page of Appendix C, and the Executive Summary's citywide PCI.  The
    # appendix prints integer PCIs while StreetSaver averages unrounded values,
    # so the citywide PCI is checked to within a point rather than exactly.
    EXPECT_SECTIONS, EXPECT_AREA, EXPECT_LENGTH, EXPECT_PCI = 3971, 149_446_612, 4_465_574, 60
    area = sum(r["area_sqft"] or 0 for r in out)
    length = sum(r["length_ft"] or 0 for r in out)
    wmean = sum(r["pci"] * (r["area_sqft"] or 0) for r in out) / area
    checks = [
        ("sections",        len(out),      EXPECT_SECTIONS, 0.0),
        ("total area ft2",  round(area),   EXPECT_AREA,     0.005),
        ("total length ft", round(length), EXPECT_LENGTH,   0.005),
        ("weighted PCI",    wmean,         EXPECT_PCI,      1.0 / EXPECT_PCI),
    ]
    ok = True
    for name, got, want, tol in checks:
        delta = abs(got - want) / want
        flag = "OK " if delta <= tol else "BAD"
        ok &= delta <= tol
        print(f"{flag} {name:16s} {got:>12,.0f}  (report: {want:,})")
    blank = sum(1 for r in out if not r["street_name"])
    print(f"    weighted PCI (exact) {wmean:.2f};  rows with no street name: {blank}")
    if not ok or blank:
        raise SystemExit("parse failed validation")

    with open(out_csv, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=[c for c, _ in COLUMNS])
        wr.writeheader()
        wr.writerows(out)
    print(f"wrote {out_csv}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
