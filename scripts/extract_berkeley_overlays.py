"""Extract Berkeley's FY2027-31 paving plans and moratorium street list.

Both PDFs are ruled tables that pdfplumber can detect directly, which matters
here because cells wrap freely ("NORTH CITY\\nLIMIT") and the plan re-lays its
columns on every page.  The PCI appendices in extract_berkeley.py cannot be
read this way -- they have no cell borders -- hence the different approach.

Output rows carry street + from/to, ready for the centerline matcher.
"""
import csv, re, sys, collections
import pdfplumber
from pdf_table import group_lines, make_column_cutter

# The moratorium list has no cell borders, so it is cut on word positions
# (its columns, unlike the plan's, are identical on every page).
MORAT_COLUMNS = [
    ("street_name", 150), ("beg_location", 260), ("end_location", 420),
    ("begins", 490), ("ends", 10_000),
]

TREATMENTS = {
    "lightmtce": "Light Maintenance", "heavyrehab": "Heavy Rehab",
    "reconstruct": "Reconstruct", "seal": "Seal", "overlay": "Overlay",
    "heavymtce": "Heavy Maintenance", "thinoverlay": "Thin Overlay",
    "light": "Light Maintenance", "heavy": "Heavy Rehab",
}


def cell(text):
    return " ".join((text or "").split())


def clean_treatment(text):
    key = re.sub(r"[^a-z]", "", (text or "").lower())
    return TREATMENTS.get(key, cell(text))


def extract_plan(path):
    rows, fy, plan = [], None, "5-Year Rehab"
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            m = re.search(r"Fiscal Year (\d{4})", text)
            if m:
                fy = int(m.group(1))
            # Continuation pages carry only the project title, not the plan
            # banner, so the plan sticks until the other banner appears.
            upper = text.upper()
            if "MEASURE FF" in upper:
                plan = "Measure FF"
            elif "5-YEAR STREET REHABILITATION" in upper:
                plan = "5-Year Rehab"
            for table in page.find_tables():
                for raw in table.extract():
                    vals = [cell(c) for c in raw]
                    if len(vals) < 7:
                        continue
                    street, beg, end, pci, miles, treat, cost = vals[:7]
                    if street.upper() in ("STREET NAME", "ROAD") or not street:
                        continue
                    if not re.fullmatch(r"\d{1,3}", pci) or "$" not in cost:
                        continue
                    rows.append({
                        "plan": plan, "fiscal_year": fy, "street_name": street,
                        "beg_location": beg, "end_location": end, "pci": int(pci),
                        "miles": float(miles) if re.fullmatch(r"[\d.]+", miles) else None,
                        "treatment": clean_treatment(treat),
                        "cost": cost.replace(" ", ""),
                    })
    return rows


def extract_moratorium(path):
    cut = make_column_cutter(MORAT_COLUMNS)
    rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for words in group_lines(page.extract_words()):
                row = cut(words)
                street, beg, end = (row["street_name"], row["beg_location"], row["end_location"])
                begins, ends = row["begins"], row["ends"]
                if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", ends or ""):
                    continue
                # "Acton St | Parker to Derby |" puts both limits in one cell.
                if not end and " to " in beg:
                    beg, end = (p.strip() for p in beg.split(" to ", 1))
                rows.append({"street_name": street, "beg_location": beg,
                             "end_location": end, "begins": begins, "ends": ends})
    return rows


def write(rows, fields, path):
    with open(path, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote {path} ({len(rows)} rows)")


def check_plan(pdf_path, rows):
    """Compare per-year counts and mileage against each page's own header."""
    stated = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = " ".join((page.extract_text() or "").split())
            m = re.search(r"(MEASURE FF|5-YEAR STREET REHABILITATION).*?Fiscal Year (\d{4})"
                          r".*?(\d+) Sections.*?([\d.]+) miles", t)
            if m:
                plan = "Measure FF" if m.group(1) == "MEASURE FF" else "5-Year Rehab"
                stated[(plan, int(m.group(2)))] = (int(m.group(3)), float(m.group(4)))
    got = collections.defaultdict(lambda: [0, 0.0])
    for r in rows:
        g = got[(r["plan"], r["fiscal_year"])]
        g[0] += 1
        g[1] += r["miles"] or 0
    ok = True
    for k in sorted(stated):
        n, mi = stated[k]
        gn, gmi = got.get(k, (0, 0))
        good = gn == n and abs(gmi - mi) <= 0.1   # per-row mileage is rounded
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} {k[0]:14s} FY{k[1]}: "
              f"{gn:3d} sections / {gmi:5.2f} mi   (stated {n} / {mi})")
    return ok


if __name__ == "__main__":
    plan_pdf, morat_pdf, plan_csv, morat_csv = sys.argv[1:5]
    plan = extract_plan(plan_pdf)
    print(f"plan: {len(plan)} projects, {sum(r['miles'] or 0 for r in plan):.2f} miles")
    if not check_plan(plan_pdf, plan):
        raise SystemExit("plan extraction disagrees with the PDF's own section counts")
    write(plan, ["plan", "fiscal_year", "street_name", "beg_location", "end_location",
                 "pci", "miles", "treatment", "cost"], plan_csv)
    morat = extract_moratorium(morat_pdf)
    # Cross-check against a plain text scan for lines carrying two dates.
    with pdfplumber.open(morat_pdf) as pdf:
        expect = sum(1 for pg in pdf.pages for ln in (pg.extract_text() or "").split("\n")
                     if len(re.findall(r"\d{1,2}/\d{1,2}/\d{4}", ln)) == 2)
    print(f"moratorium: {len(morat)} rows (text scan sees {expect} dated lines)")
    if len(morat) != expect:
        raise SystemExit("moratorium extraction missed rows")
    write(morat, ["street_name", "beg_location", "end_location", "begins", "ends"], morat_csv)
