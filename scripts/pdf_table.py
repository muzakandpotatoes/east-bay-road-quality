"""Shared helpers for reading the ruled, multi-page StreetSaver appendix tables.

Both reports flatten badly under naive text extraction: street names and
cross-street limits contain spaces, so columns have to be cut geometrically.
Words are assigned to columns by x-midpoint, and rows are recovered by
clustering words on their y position -- a fixed rounding grid splits rows,
because some cells render up to ~0.5pt below their row's baseline.
"""

LINE_TOLERANCE = 3.0  # pt; intra-row jitter is <= 0.51, real line gaps >= 9.2


def group_lines(words, tol=LINE_TOLERANCE):
    """Cluster words into visual lines by y position."""
    lines = []
    for w in sorted(words, key=lambda w: w["top"]):
        if lines and abs(w["top"] - lines[-1][0]) <= tol:
            lines[-1][1].append(w)
        else:
            lines.append((w["top"], [w]))
    return [words for _, words in lines]


def make_column_cutter(columns):
    """columns: [(name, x_midpoint_upper_bound), ...] in left-to-right order."""
    def cut(words):
        cells = {name: [] for name, _ in columns}
        for w in sorted(words, key=lambda w: w["x0"]):
            x = (w["x0"] + w["x1"]) / 2
            for name, bound in columns:
                if x < bound:
                    cells[name].append(w["text"])
                    break
            else:
                cells[columns[-1][0]].append(w["text"])
        return {k: " ".join(v).strip() for k, v in cells.items()}
    return cut
