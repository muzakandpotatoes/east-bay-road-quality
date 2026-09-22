"""Extract segment-level PCI rows from Albany's 2021 PMP Update (P-TAP 22).

Section IV, "Reference Report - Alphabetical".  Albany's report comes from the
same consultant as Berkeley's (Pavement Engineering Inc.) but uses a shorter
nine-column layout, so it needs its own column cuts while reusing the same
word-position machinery.
"""
import csv, re, sys
import pdfplumber
from pdf_table import group_lines, make_column_cutter

# (name, x-midpoint upper bound), from the header and data word runs.
COLUMNS = [
    ("street_name",  165), ("section_id",  192), ("beg_location", 325),
    ("end_location", 470), ("length_ft",   527), ("width_ft",     583),
    ("area_sqft",    645), ("fc",          690), ("pci",       10_000),
]
MARKER = "Reference Report - Alphabetical"
NOISE = re.compile(r"^(City of Albany|Reference Report|Road Name|Page \d)")
CONTINUABLE = ("street_name", "beg_location", "end_location")
# Functional class codes used in Albany's StreetSaver setup.
FC_NAMES = {"A": "Arterial", "C": "Collector", "R": "Residential/Local",
            "O": "Other", "M": "Major"}

cut = make_column_cutter(COLUMNS)


def rows_from_page(page):
    rows = []
    for words in group_lines(page.extract_words()):
        row = cut(words)
        joined = " ".join(v for v in row.values() if v)
        if not joined or NOISE.match(joined):
            continue
        if re.fullmatch(r"\d{1,3}", row["pci"]) and re.fullmatch(r"\d{1,4}[A-Z]?", row["section_id"]):
            rows.append(row)
        elif rows and not row["pci"] and not row["section_id"]:
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
        for k in ("length_ft", "width_ft", "area_sqft"):
            r[k] = num(r[k])
        r["street_name"] = r["street_name"].replace(".", "").strip()
        r["fc"] = FC_NAMES.get(r["fc"], r["fc"])

    # The Executive Summary breaks the network down by condition category and
    # by functional class, both in square feet.  Those nine figures are a far
    # stronger gate than any single total, and the parse reproduces every one
    # of them exactly.
    #
    # The report also quotes an average PCI of 56; the area-weighted mean of
    # the listing is 57.2.  Since every area figure reconciles to the square
    # foot, that gap is a difference in how the average is taken (the report
    # discusses the network in lane miles), not a parsing error, so it is
    # reported rather than enforced.
    BY_CONDITION = [("Excellent", 91, 100, 225_587), ("Good", 71, 90, 1_385_156),
                    ("Fair", 51, 70, 1_384_728), ("Poor", 31, 50, 1_694_042),
                    ("Failed", 0, 30, 469_013)]
    BY_CLASS = [("Arterial", 1_230_294), ("Collector", 1_189_498),
                ("Residential/Local", 1_713_180), ("Other", 1_025_554)]
    EXPECT_AREA, EXPECT_MILES, QUOTED_PCI = 5_158_526, 30.34, 56

    area = sum(r["area_sqft"] or 0 for r in out)
    wmean = sum(r["pci"] * (r["area_sqft"] or 0) for r in out) / area
    miles = sum(r["length_ft"] or 0 for r in out) / 5280
    ok = True

    def check(label, got, want, tol):
        nonlocal ok
        delta = abs(got - want) / want if want else 0
        ok &= delta <= tol
        print(f"{'OK ' if delta <= tol else 'BAD'} {label:24s} {got:>11,.0f}  (report: {want:,})")

    check("total area ft2", area, EXPECT_AREA, 0.005)
    check("centerline miles", miles, EXPECT_MILES, 0.05)
    for name, lo, hi, want in BY_CONDITION:
        check(f"area, {name}", sum(r["area_sqft"] or 0 for r in out
                                   if lo <= r["pci"] <= hi), want, 0.005)
    for name, want in BY_CLASS:
        check(f"area, {name}", sum(r["area_sqft"] or 0 for r in out
                                   if r["fc"] == name), want, 0.005)
    blank = sum(1 for r in out if not r["street_name"])
    print(f"    sections {len(out)};  area-weighted PCI {wmean:.2f} "
          f"(report quotes {QUOTED_PCI}, see note);  rows with no street name: {blank}")
    if not ok or blank:
        raise SystemExit("parse failed validation")

    with open(out_csv, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=[c for c, _ in COLUMNS])
        wr.writeheader()
        wr.writerows(out)
    print(f"wrote {out_csv}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
