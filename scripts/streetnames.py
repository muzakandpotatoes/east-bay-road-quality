"""Street-name normalisation shared by the PCI tables and the centerline layers.

The PCI tables and the GIS layers spell the same street differently: "AV" vs
"AVE" vs "AVENUE", "1 AV" vs "FIRST AVE" vs "1ST AVE", "M.L. KING WAY" vs
"MARTIN LUTHER KING JR WAY", directional suffixes in parentheses, and so on.
Everything is folded to a single canonical form: upper case, no punctuation,
numeric ordinals as digits ("1ST"), and a canonical suffix token.
"""
import re

SUFFIXES = {
    "AV": "AVE", "AVE": "AVE", "AVEN": "AVE", "AVENUE": "AVE",
    "ST": "ST", "STR": "ST", "STREET": "ST",
    "BV": "BLVD", "BL": "BLVD", "BLV": "BLVD", "BLVD": "BLVD", "BOULEVARD": "BLVD",
    "WY": "WAY", "WAY": "WAY",
    "DR": "DR", "DRIVE": "DR",
    "RD": "RD", "ROAD": "RD",
    "LN": "LN", "LANE": "LN",
    "PL": "PL", "PLACE": "PL",
    "CT": "CT", "COURT": "CT",
    "CR": "CIR", "CIR": "CIR", "CIRCLE": "CIR",
    "TER": "TER", "TERR": "TER", "TERRACE": "TER",
    "PKWY": "PKWY", "PY": "PKWY", "PARKWAY": "PKWY",
    "HWY": "HWY", "HIGHWAY": "HWY",
    "SQ": "SQ", "SQUARE": "SQ",
    "PATH": "PATH", "WALK": "WALK", "STEPS": "STEPS", "PZ": "PLZ", "PLZ": "PLZ",
    "EXPY": "EXPY", "EXPRESSWAY": "EXPY", "PARK": "PARK", "LOOP": "LOOP",
    "ALY": "ALY", "ALLEY": "ALY", "MALL": "MALL", "MEWS": "MEWS",
    "CRES": "CRES", "CRESCENT": "CRES", "TRL": "TRL", "TRAIL": "TRL",
}
DIRECTIONS = {"N": "N", "S": "S", "E": "E", "W": "W", "NB": "", "SB": "", "EB": "", "WB": "",
              "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
ORDINAL_WORDS = {
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH",
    "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH", "NINTH": "9TH", "TENTH": "10TH",
    "ELEVENTH": "11TH", "TWELFTH": "12TH",
}
# Streets that the two sources name completely differently.
ALIASES = {
    "M L KING JR": "MARTIN LUTHER KING JR",
    "M L KING": "MARTIN LUTHER KING JR",
    "ML KING": "MARTIN LUTHER KING JR",
    "MLK JR": "MARTIN LUTHER KING JR",
    "MARTIN LUTHER KING": "MARTIN LUTHER KING JR",
    "MARTIN LUTHER KING JR W": "MARTIN LUTHER KING JR",  # truncated in Berkeley's PDF
    "FLORANCE": "FLORENCE",                              # misspelled in Berkeley's PDF
    "M L KING JR WAY": "MARTIN LUTHER KING JR WAY",
    "M L KING WAY": "MARTIN LUTHER KING JR WAY",
    "ML KING WAY": "MARTIN LUTHER KING JR WAY",
    "MLK JR WAY": "MARTIN LUTHER KING JR WAY",
    "MARTIN LUTHER KING WAY": "MARTIN LUTHER KING JR WAY",
}


# Applied to the fully normalised string, for cases where the two sources
# disagree about the suffix itself (Berkeley's PDF truncates "WAY" to "W").
FULL_ALIASES = {
    "MARTIN LUTHER KING JR": "MARTIN LUTHER KING JR WAY",
}


def _ordinal(tok):
    """1 -> 1ST, 2 -> 2ND, 10 -> 10TH (leaves non-numeric tokens alone)."""
    if not tok.isdigit():
        return ORDINAL_WORDS.get(tok, tok)
    n = int(tok)
    if 10 <= n % 100 <= 20:
        suf = "TH"
    else:
        suf = {1: "ST", 2: "ND", 3: "RD"}.get(n % 10, "TH")
    return f"{n}{suf}"


def normalize(name, numeric_as_ordinal=True):
    if not name:
        return ""
    s = name.upper()
    s = re.sub(r"\(.*?\)", " ", s)             # "(SB)", "(EB)" and similar notes
    s = s.replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)          # M.L. -> M L,  O'FARRELL -> O FARRELL
    toks = [t for t in s.split() if t]
    if not toks:
        return ""

    # A trailing direction ("SHATTUCK AVE SB") is noise for matching purposes.
    while len(toks) > 1 and toks[-1] in DIRECTIONS and not DIRECTIONS[toks[-1]]:
        toks.pop()

    suffix = ""
    if len(toks) > 1 and toks[-1] in SUFFIXES:
        suffix = SUFFIXES[toks.pop()]

    # "MC GEE" and "MCGEE" are the same street.
    if len(toks) > 1 and toks[0] == "MC":
        toks = [toks[0] + toks[1]] + toks[2:]
    # Leading article or direction: the two sources disagree freely about both
    # ("EAST BOLIVAR DR" vs "BOLIVAR DR", "W FRONTAGE RD" vs "FRONTAGE RD").
    while len(toks) > 1 and (toks[0] == "THE" or toks[0] in DIRECTIONS):
        toks.pop(0)

    if numeric_as_ordinal:
        toks = [_ordinal(t) for t in toks]

    core = ALIASES.get(" ".join(toks), " ".join(toks))
    out = f"{core} {suffix}".strip()
    return FULL_ALIASES.get(out, out)


def variants(name):
    """Canonical form plus a suffix-free form, for looser fallback matching."""
    full = normalize(name)
    return {full, " ".join(full.split()[:-1]) if len(full.split()) > 1 else full}
