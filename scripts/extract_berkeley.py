"""Extract segment-level PCI rows from Berkeley's 2024 PMP Update (P-TAP 25).

Section IV, "Reference Reports: Street Sections Alphabetical" (pages 40-67).
"""
import csv, re, sys
import pdfplumber
from pdf_table import group_lines, make_column_cutter

# (name, x-midpoint upper bound), derived from the header and data word runs.
COLUMNS = [
    ("district",      48), ("street_id",     86), ("street_name",  210),
    ("section_id",   242), ("beg_location", 428), ("end_location", 593),
    ("fc",           609), ("surface_type", 628), ("lanes",        650),
    ("length_ft",    688), ("width_ft",     714), ("cl_miles",     742),
    ("ln_miles",     768), ("area_sqft",    802), ("pci",          832),
    ("pci_date",     890), ("condition",    958), ("pci_range",   1000),
    ("last_mnr_date", 1075), ("last_mnr_treatment", 10_000),
]
MARKER = "Reference Reports: Street Sections Alphabetical"
NOISE = re.compile(r"^(City of Berkeley|Reference Reports|STREET|District|ID\b|Page \d)")
CONTINUABLE = ("street_name", "beg_location", "end_location", "last_mnr_treatment")

cut = make_column_cutter(COLUMNS)

# Functional class and surface type codes, per the report's own glossary
# (Section V).  The expanded surface names match Oakland's vocabulary.
FC_NAMES = {"A": "Arterial", "C": "Collector", "R": "Residential/Local"}
SURFACE_NAMES = {"A": "AC", "C": "AC/PCC", "O": "AC/AC", "P": "PCC", "S": "ST"}


def rows_from_page(page):
    rows = []
    for words in group_lines(page.extract_words()):
        row = cut(words)
        joined = " ".join(v for v in row.values() if v)
        if not joined or NOISE.match(joined):
            continue
        if re.fullmatch(r"\d{1,3}", row["pci"]) and re.fullmatch(r"\d{4,7}", row["street_id"]):
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
        for k in ("length_ft", "width_ft", "area_sqft", "lanes", "cl_miles", "ln_miles"):
            r[k] = num(r[k])
        r["condition"] = re.sub(r"\s*\(.*\)", "", r["condition"])
        r["fc"] = FC_NAMES.get(r["fc"], r["fc"])
        r["surface_type"] = SURFACE_NAMES.get(r["surface_type"], r["surface_type"])

    # Control totals: the Executive Summary's network area and citywide PCI.
    # (Berkeley's appendix prints no totals row, unlike Oakland's.)
    EXPECT_AREA, EXPECT_PCI, EXPECT_SECTIONS = 39_363_218, 56, 1216
    area = sum(r["area_sqft"] or 0 for r in out)
    wmean = sum(r["pci"] * (r["area_sqft"] or 0) for r in out) / area
    checks = [
        ("sections",       len(out),    EXPECT_SECTIONS, 0.02),
        ("total area ft2", round(area), EXPECT_AREA,     0.005),
        ("weighted PCI",   wmean,       EXPECT_PCI,      1.0 / EXPECT_PCI),
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
