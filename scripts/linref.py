"""Locate "street + from/to cross streets" records on a centerline network.

Berkeley publishes no pavement-section geometry (unlike Oakland, whose GIS
carries the StreetSaver section ids), so its PCI rows -- and its paving-plan
and moratorium lists, which have the same shape -- have to be placed on the
centerline network:

  1. Build a graph whose nodes are snapped centerline endpoints and whose
     edges are centerline segments.  A proper centerline layer is already
     split at intersections, so an intersection is just a shared node.
  2. For a PCI row, an "anchor" for a cross street is any node on the subject
     street that is also touched by an edge carrying the cross street's name.
  3. The section geometry is the shortest path along the subject street's own
     edges between the begin and end anchors.
  4. Where a street meets a cross street more than once, every anchor pair is
     tried and the one whose path length best matches the length printed in
     the report wins.  That reported length then doubles as the accept test.
"""
import json, math, os, re, heapq, collections
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree
from streetnames import normalize

FT_PER_M = 3.280839895
SNAP_M = 2.0          # endpoints within this distance are the same node
NODE_M = 2.0          # a cross street ending this close to a segment splits it
BRIDGE_M = 30.0       # close a same-street gap up to this wide (see _bridge_gaps)
LENGTH_TOL = 0.35     # max relative disagreement with the reported length

# Limits that name the end of a street rather than a cross street.
TERMINALS = {
    "CITY LIMIT", "CITY LIMITS", "END", "DEAD END", "DEADEND", "CUL DE SAC", "CDS",
    "TERMINUS", "BEGIN", "START", "NORTH END", "SOUTH END", "EAST END", "WEST END",
    "EAST CITY LIMIT", "WEST CITY LIMIT", "NORTH CITY LIMIT", "SOUTH CITY LIMIT",
    "PAVEMENT END", "END OF PAVEMENT", "END OF STREET", "GATE",
}

to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26910", always_xy=True).transform
to_wgs = Transformer.from_crs("EPSG:26910", "EPSG:4326", always_xy=True).transform


def is_terminal(label):
    """True when a from/to label names the end of the street, not a cross street."""
    s = (label or "").upper()
    s = re.sub(r"\(.*?\)", " ", s)                  # "DEAD END (ACTON ST)"
    s = " ".join(s.replace(".", " ").replace("-", " ").split())
    if not s or s in TERMINALS:
        return True
    return bool(
        "CITY LIMIT" in s or "CUL DE SAC" in s or "DEAD END" in s
        or s.endswith(" END") or s.startswith("END ") or s.startswith("BEG ")
        or s.startswith("BEGIN ") or s.startswith("START ")
    )


OFFSET_RE = re.compile(r"^\s*\d+\s*'?\s*(?:FT\.?|FEET)?\s*[NSEW]{1,2}\s*/?O\s+(?P<street>.+)$")


def label_alternatives(label):
    """Street names a from/to label might refer to, best guess first.

    Labels are not always plain cross streets: they carry notes in parentheses
    ("CITY LIMIT (DOVER ST)" -- where the parenthetical is the real cross
    street), name two streets ("MLK/ ADELINE ST"), or give an offset from one
    ("374' E/O MARTIN LUTHER KING JR WAY").
    """
    raw = (label or "").upper()
    cands = []
    for chunk in re.findall(r"\((.*?)\)", raw):          # parentheticals first
        cands.append(chunk)
    cands.append(re.sub(r"\(.*?\)", " ", raw))
    out = []
    for c in cands:
        c = re.sub(r"^\s*(OPP|OPPOSITE|APPROX|NEAR|AT)\s+", " ", c)
        for piece in re.split(r"[/&,]| AND ", c):
            piece = piece.strip(" .")
            if not piece:
                continue
            m = OFFSET_RE.match(piece)
            if m:
                piece = m.group("street").strip()
            if piece and piece not in out:
                out.append(piece)
    return out


def is_soft(label):
    """A label that does not pin an exact point (terminal, or an offset)."""
    return is_terminal(label) or bool(OFFSET_RE.match(
        re.sub(r"\(.*?\)", " ", (label or "").upper()).strip()))


def _substring(line, a, b):
    """Coordinates of `line` between distances a and b along it."""
    pts = [line.interpolate(a).coords[0]]
    for c in line.coords:
        d = line.project(Point(c))
        if a < d < b:
            pts.append(c)
    pts.append(line.interpolate(b).coords[0])
    dedup = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, dedup[-1]) > 1e-6:
            dedup.append(p)
    return dedup


def load_centerlines(city_path, osm_path=None, municipality=None):
    """City centerlines, topped up from OSM only where a name is absent.

    The city layer stays authoritative: an OSM way is admitted only when its
    normalised name appears nowhere in the city layer, so the two sources can
    never supply competing geometry for the same street.

    `municipality` keeps only that city's own streets, which matters when the
    layer spans several cities and street names repeat across them.  The
    filter is applied *before* deciding what OSM should fill in -- otherwise a
    street the filter removes still counts as "present" and OSM is refused,
    which silently loses it (Albany's Ramona Ave is tagged Contra Costa in
    Berkeley's layer and vanished exactly this way).
    """
    feats = json.load(open(city_path))["features"]
    if municipality:
        feats = [f for f in feats
                 if municipality in ((f["properties"] or {}).get("MUNILEFT"),
                                     (f["properties"] or {}).get("MUNIRIGHT"))]
    if not osm_path or not os.path.exists(osm_path):
        return feats, 0
    have = {normalize((f["properties"] or {}).get("FULLNAME")) for f in feats}
    have.discard("")
    added = [f for f in json.load(open(osm_path))["features"]
             if normalize((f["properties"] or {}).get("FULLNAME")) not in have]
    return feats + added, len({normalize(f["properties"]["FULLNAME"]) for f in added})


def parts(geom):
    """Every LineString in a GeoJSON geometry, as coordinate lists."""
    if not geom:
        return []
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return list(geom["coordinates"])
    return []


class Network:
    @staticmethod
    def _noded(raw):
        """Split each segment wherever another segment's endpoint touches it.

        Centerline layers are split at crossings but often not at T
        intersections, so a side street can end part-way along a block with no
        shared node.  Without this the block cannot be addressed by its cross
        streets at all.
        """
        lines = [LineString(pts) for _, pts in raw]
        ends = []
        for ln in lines:
            ends.append(Point(ln.coords[0]))
            ends.append(Point(ln.coords[-1]))
        tree = STRtree(ends)
        out = []
        for (name, pts), ln in zip(raw, lines):
            cuts = set()
            for j in tree.query(ln.buffer(NODE_M)):
                p = ends[j]
                if ln.distance(p) > NODE_M:
                    continue
                d = ln.project(p)
                if NODE_M < d < ln.length - NODE_M:
                    cuts.add(round(d, 2))
            if not cuts:
                out.append((name, pts))
                continue
            bounds = [0.0] + sorted(cuts) + [ln.length]
            for a, b in zip(bounds, bounds[1:]):
                if b - a < 0.5:
                    continue
                seg = _substring(ln, a, b)
                if len(seg) >= 2:
                    out.append((name, seg))
        return out

    def __init__(self, features, name_field):
        self.nodes = {}                              # snapped key -> node id
        self.coords = []                             # node id -> (x, y)
        self.edges = []                              # (u, v, length_m, name, [(x,y)...])
        self.by_name = collections.defaultdict(list)
        self.incident = collections.defaultdict(set)  # node id -> {edge index}
        self.names_at = collections.defaultdict(set)  # node id -> {street names}

        raw = []
        for feat in features:
            name = normalize((feat["properties"] or {}).get(name_field))
            if not name:
                continue
            for path in parts(feat.get("geometry")):
                pts = [to_utm(x, y) for x, y, *_ in path]
                if len(pts) >= 2:
                    raw.append((name, pts))
        raw = self._noded(raw)

        for name, pts in raw:
            if True:
                u, v = self._node(pts[0]), self._node(pts[-1])
                if u == v:
                    continue
                length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
                idx = len(self.edges)
                self.edges.append((u, v, length, name, pts))
                self.by_name[name].append(idx)
                self.incident[u].add(idx)
                self.incident[v].add(idx)
                self.names_at[u].add(name)
                self.names_at[v].add(name)

        self._bridge_gaps()

        self.cores = collections.defaultdict(set)
        for name in self.by_name:
            self.cores[" ".join(name.split()[:-1]) or name].add(name)

    def _components(self, edge_idxs):
        """Connected node sets within one street's own edges."""
        adj = collections.defaultdict(set)
        nodes = set()
        for i in edge_idxs:
            u, v = self.edges[i][0], self.edges[i][1]
            adj[u].add(v)
            adj[v].add(u)
            nodes |= {u, v}
        seen, comps = set(), []
        for n in nodes:
            if n in seen:
                continue
            stack, comp = [n], set()
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                stack.extend(y for y in adj[x] if y not in comp)
            seen |= comp
            comps.append(comp)
        return comps

    def _bridge_gaps(self):
        """Join pieces of the same street separated by a small gap.

        The centerline layer stops many streets at the kerb line rather than
        the intersection centre, leaving 13-25 m holes where a cross street
        passes -- 37% of street names arrive here in more than one piece.  A
        section spanning such a hole has no path between its cross streets and
        would be dropped.  Only same-named pieces are joined, and only across
        gaps far shorter than a block, so this cannot connect two streets.
        """
        bridged = 0
        for name, edge_idxs in list(self.by_name.items()):
            comps = self._components(edge_idxs)
            while len(comps) > 1:
                best = None
                for a in range(len(comps)):
                    for b in range(a + 1, len(comps)):
                        for p in comps[a]:
                            for q in comps[b]:
                                d = math.dist(self.coords[p], self.coords[q])
                                if best is None or d < best[0]:
                                    best = (d, a, b, p, q)
                if best is None or best[0] > BRIDGE_M:
                    break
                d, a, b, p, q = best
                idx = len(self.edges)
                self.edges.append((p, q, d, name, [self.coords[p], self.coords[q]]))
                self.by_name[name].append(idx)
                self.incident[p].add(idx)
                self.incident[q].add(idx)
                self.names_at[p].add(name)
                self.names_at[q].add(name)
                comps[a] |= comps[b]
                comps.pop(b)
                bridged += 1
        self.bridged = bridged

    def _node(self, pt):
        key = (round(pt[0] / SNAP_M), round(pt[1] / SNAP_M))
        if key not in self.nodes:
            self.nodes[key] = len(self.coords)
            self.coords.append(pt)
        return self.nodes[key]

    def street_edges(self, name):
        """Edges for a street name, falling back to a suffix-insensitive match."""
        if name in self.by_name:
            return self.by_name[name], name
        core = " ".join(name.split()[:-1]) or name
        alts = self.cores.get(core) or self.cores.get(name)
        if alts:
            idxs = [i for a in alts for i in self.by_name[a]]
            return idxs, sorted(alts)[0]
        return [], None

    def shortest_path(self, edge_idxs, src, dst):
        """Dijkstra over a restricted edge set; returns (length_m, [edge ids])."""
        adj = collections.defaultdict(list)
        for i in edge_idxs:
            u, v, w, _, _ = self.edges[i]
            adj[u].append((v, w, i))
            adj[v].append((u, w, i))
        dist = {src: 0.0}
        prev = {}
        pq = [(0.0, src)]
        while pq:
            d, n = heapq.heappop(pq)
            if n == dst:
                break
            if d > dist.get(n, math.inf):
                continue
            for m, w, i in adj[n]:
                nd = d + w
                if nd < dist.get(m, math.inf):
                    dist[m] = nd
                    prev[m] = (n, i)
                    heapq.heappush(pq, (nd, m))
        if dst not in dist:
            return None, None
        chain, cur = [], dst
        while cur != src:
            cur, i = prev[cur]
            chain.append(i)
        return dist[dst], chain[::-1]


def anchors_for(net, edge_idxs, label, street_name):
    """Nodes on this street where the given cross street also arrives."""
    nodes = set()
    for i in edge_idxs:
        u, v, *_ = net.edges[i]
        nodes.add(u)
        nodes.add(v)

    # A named cross street always wins, even inside a "CITY LIMIT (DOVER ST)"
    # style label, because it pins an exact point.
    named = _named_anchors(net, nodes, label, street_name)
    if named:
        return named, False

    if is_terminal(label):
        # A terminal limit is an end of the street: a node with only one of
        # the street's own edges attached.
        degree = collections.Counter()
        for i in edge_idxs:
            u, v, *_ = net.edges[i]
            degree[u] += 1
            degree[v] += 1
        return sorted(n for n in nodes if degree[n] == 1), True

    return [], False


def _named_anchors(net, nodes, label, street_name):
    wants = {normalize(a) for a in label_alternatives(label)}
    wants.discard("")
    if not wants:
        return []
    cores = {" ".join(w.split()[:-1]) or w for w in wants}
    hits = []
    for n in nodes:
        here = net.names_at[n]
        if here & wants or any(
                (" ".join(h.split()[:-1]) or h) in cores and h != street_name for h in here):
            hits.append(n)
    return sorted(hits)


def terminal_nodes(net, edge_idxs):
    degree = collections.Counter()
    for i in edge_idxs:
        u, v, *_ = net.edges[i]
        degree[u] += 1
        degree[v] += 1
    return sorted(n for n, d in degree.items() if d == 1)


def walk_candidates(net, edge_idxs, src, target_m):
    """Every direction from src that can accommodate `target_m`.

    Returns a list of coordinate lists, one per direction out of the anchor.
    More than one means the block could sit on either side of the
    intersection; the caller decides, and refuses rather than guessing.
    """
    adj = collections.defaultdict(list)
    for i in edge_idxs:
        u, v, w, _, _ = net.edges[i]
        adj[u].append((v, w, i))
        adj[v].append((u, w, i))

    best = {}                       # first edge out of src -> (err, length, node)
    dist, prev, first = {src: 0.0}, {}, {}
    pq = [(0.0, src)]
    while pq:
        d, n = heapq.heappop(pq)
        if d > dist.get(n, math.inf) or d > target_m * 2.5:
            continue
        for m, w, i in adj[n]:
            nd = d + w
            if nd < dist.get(m, math.inf):
                dist[m] = nd
                prev[m] = (n, i)
                first[m] = i if n == src else first[n]
                heapq.heappush(pq, (nd, m))
    for n, d in dist.items():
        if n == src or d < target_m * 0.9:
            continue
        err = abs(d - target_m) / target_m
        f = first[n]
        if f not in best or err < best[f][0]:
            best[f] = (err, d, n)
    out = []
    for err, _, dst in best.values():
        if err > LENGTH_TOL:
            continue
        chain, cur = [], dst
        while cur != src:
            cur, i = prev[cur]
            chain.append(i)
        coords = chain_coords(net, chain[::-1], src)
        out.append(_substring(LineString(coords), 0, target_m))
    return out


def chain_coords(net, chain, src):
    """Stitch an edge chain into one ordered coordinate list starting at src."""
    coords, cur = [], src
    for i in chain:
        u, v, _, _, pts = net.edges[i]
        seq = pts if u == cur else pts[::-1]
        cur = v if u == cur else u
        coords.extend(seq[1:] if coords else seq)
    return coords


def _overlaps(coords, occupied, tol=8.0):
    """Does this candidate mostly retrace geometry already claimed nearby?"""
    if occupied is None:
        return False
    line = LineString(coords)
    if line.length == 0:
        return False
    return line.intersection(occupied.buffer(tol)).length > 0.5 * line.length


def match_row(net, street_name, beg_location, end_location, length_ft=None,
              occupied=None):
    """Locate one record on the network; returns (coords, diagnostics).

    `length_ft` is the length the source reports for the section.  When it is
    known it both disambiguates repeated intersections and gates the result;
    when it is not (the moratorium list prints no lengths) the shortest path
    between the two cross streets is taken instead.
    """
    name = normalize(street_name)
    edge_idxs, resolved = net.street_edges(name)
    if not edge_idxs:
        return None, {"reason": "street name not in centerlines"}

    target_m = (float(length_ft) / FT_PER_M) if length_ft else None

    beg, _ = anchors_for(net, edge_idxs, beg_location, resolved)
    end, _ = anchors_for(net, edge_idxs, end_location, resolved)
    soft_beg, soft_end = is_soft(beg_location), is_soft(end_location)

    # "ACROFT CT: ACTON ST -> DEAD END (ACTON ST)": the parenthetical names the
    # street the cul-de-sac hangs off, not a second intersection.  When both
    # ends land on the same node, re-read the terminal end as a true terminal.
    if beg and end and set(beg) == set(end):  # noqa: E501
        if soft_end:
            end = terminal_nodes(net, edge_idxs)
        elif soft_beg:
            beg = terminal_nodes(net, edge_idxs)

    cands = []
    for s_node in beg:
        for t_node in end:
            if s_node == t_node:
                continue
            length, chain = net.shortest_path(edge_idxs, s_node, t_node)
            if chain is not None:
                cands.append((length, chain, s_node))

    if cands:
        if target_m is None:
            # No reported length: the block between the two cross streets is
            # the shortest route along the street that joins them.
            length, chain, s_node = min(cands, key=lambda c: c[0])
            coords = chain_coords(net, chain, s_node)
            return coords, {"reason": "ok", "length_err": None,
                            "matched_ft": round(length * FT_PER_M),
                            "method": "cross-streets"}
        if soft_beg or soft_end:
            over = [c for c in cands if c[0] >= target_m * 0.98]
            length, chain, s_node = (min(over, key=lambda c: c[0]) if over
                                     else min(cands, key=lambda c: abs(c[0] - target_m)))
        else:
            length, chain, s_node = min(cands, key=lambda c: abs(c[0] - target_m))

        coords = chain_coords(net, chain, s_node)
        trimmed = False
        if (soft_beg or soft_end) and length > target_m * 1.02:
            line = LineString(coords)
            coords = (_substring(line, line.length - target_m, line.length)
                      if soft_beg and not soft_end else _substring(line, 0, target_m))
            length = LineString(coords).length
            trimmed = True
        err = abs(length - target_m) / target_m
        diag = {"reason": "ok", "length_err": round(err, 3),
                "matched_ft": round(length * FT_PER_M),
                "method": "trimmed" if trimmed else "cross-streets"}
        if err > LENGTH_TOL:
            diag["reason"] = f"length mismatch ({err:.0%})"
            return None, diag
        return coords, diag

    # Only one end is a recognisable point (the other is a park, a railway
    # crossing, a landmark, or the two ends are disconnected).  Walk the
    # reported length from the end we do know, if the direction is unambiguous.
    for anchors in ((beg, end) if target_m else ()):
        if not anchors:
            continue
        for s_node in anchors:
            cands = walk_candidates(net, edge_idxs, s_node, target_m)
            if len(cands) > 1 and occupied is not None:
                # The neighbouring sections of this street in the same table
                # already cover one side of the intersection, so the block
                # being placed must be on the other.
                cands = [c for c in cands if not _overlaps(c, occupied)]
            if len(cands) == 1:
                return cands[0], {"reason": "ok", "length_err": 0.0,
                                  "matched_ft": round(target_m * FT_PER_M),
                                  "method": "one-ended walk"}
    if not beg or not end:
        missing = "begin" if not beg else "end"
        return None, {"reason": f"{missing} cross street not found on street"}
    return None, {"reason": "no path between cross streets along this street"}


