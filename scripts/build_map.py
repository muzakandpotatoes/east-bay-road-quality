"""Assemble the single-file interactive map from the packed payload.

The basemap comes from Tracestrack, which needs an API key.  The key is read
from the TRACESTRACK_API_KEY environment variable or a local .env, never
committed -- but note it is injected into the generated HTML, so treat the
built map as containing a credential.  Without a key the build falls back to
public OpenStreetMap tiles.
"""
import json, os, re, sys

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>East Bay Street Pavement Condition</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
  :root{
    color-scheme: light;
    --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781;
    --surface:#fcfcfb; --hairline:#e1e0d9; --ring:rgba(11,11,11,.10);
    --failed:#08306b; --poor:#084a91; --atrisk:#1764ab;
    --fair:#2e7ebc; --good:#4a98c9; --vgood:#6aaed6;
    --plan:#0f8a5f; --morat:#3f4448; --bike:#d94801;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--surface)}
  #map{position:absolute;inset:0}
  .leaflet-container{background:#eeeceb;font:inherit}
  /* The basemap is flattened to a near-white grey so it sits clear of the
     ramp's lightness range (L 0.29-0.74) -- condition is read by lightness,
     so the background must not compete for it. */
  .leaflet-tile-pane{filter:grayscale(1) contrast(.62) brightness(1.22);opacity:.45}

  .panel{
    position:absolute;z-index:1000;top:calc(12px + env(safe-area-inset-top,0px));left:12px;
    width:310px;max-width:calc(100vw - 24px);max-height:calc(100% - 24px - env(safe-area-inset-top,0px));
    overflow:auto;background:var(--surface);border:1px solid var(--ring);
    border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.10)
  }
  .panel section{padding:12px 14px;border-bottom:1px solid var(--hairline)}
  .panel section:last-child{border-bottom:0}
  h1{margin:0 0 2px;font-size:15px;letter-spacing:-.01em}
  .sub{color:var(--ink-2);font-size:12px;margin:0}
  h2{margin:0 0 8px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600}

  .stats{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .stat{border:1px solid var(--hairline);border-radius:8px;padding:8px 10px}
  .stat b{display:block;font-size:19px;letter-spacing:-.02em}
  .stat span{color:var(--ink-2);font-size:11px}

  .row{display:flex;align-items:center;gap:9px;width:100%;padding:4px 6px;margin:0 -6px;
       border:0;background:none;font:inherit;color:inherit;text-align:left;cursor:pointer;border-radius:6px}
  .row:hover{background:#f2f1ee}
  .row[aria-pressed="false"]{opacity:.38}
  .swatch{width:22px;height:5px;border-radius:3px;flex:none}
  .row .name{flex:1}
  .row .num{color:var(--ink-2);font-variant-numeric:tabular-nums;font-size:12px}
  .rng{color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
  .hint{margin:8px 0 0;font-size:11px;color:var(--muted)}
  .check{display:flex;gap:7px;align-items:center;margin-top:9px;font-size:12.5px;cursor:pointer}
  .check input{margin:0;cursor:pointer}
  .swatch.bike{height:7px;border-radius:4px}

  details summary{cursor:pointer;color:var(--ink-2);font-size:12px;list-style:none}
  details summary::-webkit-details-marker{display:none}
  details summary::before{content:"▸ ";color:var(--muted)}
  details[open] summary::before{content:"▾ "}
  details p{margin:8px 0 0;font-size:11.5px;line-height:1.5;color:var(--ink-2)}
  details p b{color:var(--ink)}

  .pop h3{margin:0 0 1px;font-size:14px}
  .pop .lim{color:var(--ink-2);font-size:12px;margin:0 0 8px}
  .pop .pci{display:flex;align-items:baseline;gap:7px;margin-bottom:8px}
  .pop .pci b{font-size:26px;letter-spacing:-.02em;line-height:1}
  .pop dl{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:0;font-size:12px}
  .pop dt{color:var(--muted)}
  .pop dd{margin:0}
  .leaflet-popup-content{margin:11px 13px;min-width:210px}
  .leaflet-popup-content-wrapper{border-radius:9px}
  .tag{display:inline-block;padding:1px 6px;border-radius:999px;font-size:11px;color:#fff}

  @media (max-width:640px){
    .panel{top:auto;bottom:calc(10px + env(safe-area-inset-bottom,0px));left:10px;right:10px;width:auto;max-height:46%}
  }
</style>
</head>
<body>
<div id="map"></div>

<div class="panel">
  <section>
    <h1>East Bay street pavement condition</h1>
    <p class="sub">Pavement Condition Index by street section, Berkeley &amp; Oakland — fall 2024 StreetSaver surveys</p>
  </section>

  <section>
    <h2>Network</h2>
    <div class="stats" id="stats"></div>
  </section>

  <section>
    <h2>Condition (PCI) · darker is worse</h2>
    <div id="legend"></div>
    <p class="hint">Click a band to show or hide it.</p>
  </section>

  <section>
    <h2>Bike network (existing)</h2>
    <div id="biketiers"></div>
    <label class="check"><input type="checkbox" id="bikeonly">
      Show only streets with a bikeway</label>
    <p class="hint" id="bikestat"></p>
  </section>

  <section>
    <h2>City</h2>
    <div id="cities"></div>
  </section>

  <section>
    <h2>Context overlays</h2>
    <div id="overlays"></div>
  </section>

  <section>
    <details>
      <summary>How to read this &amp; caveats</summary>
      <p><b>PCI measures structural distress, not ride quality.</b> It scores cracking,
      patching and deformation on a 0–100 scale. A street can feel rough and still
      score well, or feel smooth over a failing base.</p>
      <p><b>Oakland's survey sampled 44.9% of network area</b> (3,304 of 3,971 sections
      inspected Sept–Dec 2024). Scores for unsampled sections are modelled from
      deterioration curves rather than observed.</p>
      <p><b>The data is fall 2024</b> and predates Berkeley's Measure FF paving spending,
      so recently repaved streets may score better today than shown.</p>
      <p><b>Being on a paving plan is not a worst-first ranking.</b> Plans reflect
      StreetSaver cost-effectiveness ranking and equity-zone priorities, so a
      mid-condition street is often treated ahead of a failed one.</p>
      <p><b>State highways are absent.</b> These reports cover only
      city-maintained pavement, so Caltrans routes are blank on the map — in
      Berkeley that means San Pablo Ave (SR-123) and Ashby Ave (SR-13), which
      appear in the data only as cross streets. Freeways are excluded too.</p>
      <p><b>Geometry is matched, not published.</b> Oakland's sections join to city
      GIS by section id. Berkeley publishes no section geometry, so its sections are
      located on the centerline network by street name and cross streets; unmatched
      sections are omitted rather than guessed. Match rates are above.</p>
      <p>Sources: Berkeley 2024 Pavement Management Plan Update (P-TAP 25) and
      Oakland P-TAP Round 25, with city ArcGIS centerline, paving-plan and
      moratorium services.</p>
    </details>
  </section>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
(function(){
  "use strict";
  const DATA = JSON.parse(document.getElementById("payload").textContent);
  const POOL = DATA.pool, SCALE = DATA.scale;

  // MTC / StreetSaver condition categories, worst to best, on a single-hue
  // blue ramp (Blues sampled over t=0.50..1.00): lightness is monotonic, so
  // condition reads as "darker is worse" before any colour is named.  The pale
  // half of the ramp is cut off because it would vanish against the basemap.
  //
  // One hue for the whole ramp is what makes the bike casing legible.  Six
  // steps spanning the lightness range leave no lightness free -- some band
  // always matches any casing -- so the casing has to be told apart by hue,
  // and a blue ramp leaves the entire warm half of the wheel for it.
  const BANDS = [
    {name:"Failed",                lo:0,  hi:24,  color:"#08306b"},
    {name:"Poor",                  lo:25, hi:49,  color:"#084a91"},
    {name:"At Risk",               lo:50, hi:59,  color:"#1764ab"},
    {name:"Fair",                  lo:60, hi:69,  color:"#2e7ebc"},
    {name:"Good",                  lo:70, hi:79,  color:"#4a98c9"},
    {name:"Very Good / Excellent", lo:80, hi:100, color:"#6aaed6"}
  ];
  const bandOf = pci => BANDS.find(b => pci >= b.lo && pci <= b.hi) || BANDS[0];

  // Bikeways are drawn as a casing *under* the PCI line, in one neutral hue:
  // condition already owns colour, and a second colour scale on the same
  // streets would be unreadable.  Tier is carried by weight and dash instead.
  // `mult` is a multiple of the PCI line weight, not a fixed pixel offset, so
  // the casing keeps the same visual ratio to the line at every zoom.  The
  // casing is fully opaque: translucent casings compounded where routes
  // crossed, darkening the intersections.
  //
  // Tier is carried mainly by how broken the casing is, which reads far more
  // easily than a width difference and matches what the tiers mean: a solid
  // edge for continuous protection, close ticks for a painted stripe, sparse
  // ticks for markings only.  `dash` is in multiples of the PCI line weight
  // too, so the pattern holds its proportions at every zoom.
  const TIERS = [
    {key:"protected", label:"Protected / off-street", mult:2.3, dash:null},
    {key:"painted",   label:"Painted bike lane",      mult:1.9, dash:[2.4, 1.1]},
    {key:"shared",    label:"Shared roadway / blvd",  mult:1.6, dash:[0.9, 2.2]}
  ];
  const dashFor = (t, w) => t.dash ? t.dash.map(d => (d * w).toFixed(1)).join(",") : null;
  const BIKE_COLOR = "#d94801";
  const tierOf = k => TIERS.find(t => t.key === k);

  function rows(layer){
    const {keys, rows, geom} = layer, out = [];
    for (let i = 0; i < rows.length; i++){
      const o = {};
      keys.forEach((k, j) => {
        const v = rows[i][j];
        o[k] = (typeof v === "number" && STRING_KEYS[k]) ? POOL[v] : v;
      });
      o._geom = geom[i];
      out.push(o);
    }
    return out;
  }
  const STRING_KEYS = {city:1,street:1,section_id:1,from:1,to:1,functional_class:1,
    surface_type:1,survey_date:1,last_treatment:1,last_treatment_date:1,match_method:1,
    label:1,detail:1,ends:1,bike_tier:1,tier:1,tier_label:1};

  // Delta-encoded integer coordinates -> Leaflet [lat, lng] rings.
  function latlngs(parts){
    return parts.map(flat => {
      const pts = []; let x = 0, y = 0;
      for (let i = 0; i < flat.length; i += 2){
        x += flat[i]; y += flat[i+1];
        pts.push([y / SCALE, x / SCALE]);
      }
      return pts;
    });
  }

  const sections = rows(DATA.sections);

  const plan     = rows(DATA.plan);
  const morat    = rows(DATA.moratorium);

  const map = L.map("map", {preferCanvas:true, zoomControl:false, minZoom:11, maxZoom:18})
               .setView([37.8195, -122.2410], 13);
  L.control.zoom({position:"topright"}).addTo(map);
  L.control.scale({imperial:true, metric:false}).addTo(map);
  L.tileLayer(__TILE_URL__, {
    maxZoom:19, attribution:__TILE_ATTR__
  }).addTo(map);

  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  const num = n => n == null ? null : Number(n).toLocaleString();

  // Line weight has to stay readable at neighbourhood zoom without smothering
  // the basemap when zoomed out to the whole city.
  const WEIGHTS = {11:1.2, 12:1.6, 13:2.2, 14:3, 15:4, 16:5.5, 17:7, 18:9};
  const weightFor = z => WEIGHTS[Math.max(11, Math.min(18, z))] || 3;

  const paneFor = (name, z) => {
    const p = map.createPane(name); p.style.zIndex = z; return p;
  };
  paneFor("moratPane", 400); paneFor("planPane", 410);
  paneFor("bikePane", 415); paneFor("pciPane", 420);

  // On-street bikeways reuse the PCI section's own geometry, so the casing
  // sits exactly under the coloured line instead of beside it.
  function casing(coords, tier, city) {
    const t = tierOf(tier) || TIERS[2];
    const line = L.polyline(coords, {
      pane:"bikePane", color:BIKE_COLOR, weight:weightFor(13) * t.mult,
      opacity:1, lineCap:"butt", dashArray:dashFor(t, weightFor(13)), interactive:false
    });
    line._tier = t;
    line._d = {tier:tier, city:city};
    return line;
  }

  const bikeLines = sections.filter(d => d.bike_tier)
                            .map(d => casing(latlngs(d._geom), d.bike_tier, d.city));

  // Off-street paths have no PCI section under them, so they carry their own
  // geometry and stay clickable.
  const offLines = rows(DATA.offstreet || {keys:[], rows:[], geom:[]}).map(d => {
    const t = tierOf(d.tier) || TIERS[0];
    const line = L.polyline(latlngs(d._geom), {
      pane:"bikePane", color:BIKE_COLOR, weight:weightFor(13) * t.mult,
      opacity:1, lineCap:"butt", dashArray:dashFor(t, weightFor(13))
    });
    line._tier = t; line._d = d;
    line.bindPopup(() =>
      `<div class="pop"><h3>${esc(d.street || t.label)}</h3>` +
      `<p class="lim">${esc(d.city)} \u00b7 off-street path</p>` +
      `<dl><dt>Type</dt><dd>${esc(d.tier_label || t.label)}</dd>` +
      `<dt>Length</dt><dd>${d.miles} mi</dd></dl></div>`, {maxWidth:300});
    return line;
  });

  const pciLines = sections.map(d => {
    const band = bandOf(d.pci);
    const line = L.polyline(latlngs(d._geom), {
      pane:"pciPane", color:band.color, weight:weightFor(13), opacity:.95, lineCap:"round"
    });
    line._d = d; line._band = band;
    line.bindPopup(() => sectionPopup(d, band), {maxWidth:340});
    return line;
  });

  const planLines = plan.map(d => {
    const line = L.polyline(latlngs(d._geom),
      {pane:"planPane", color:"#0f8a5f", weight:weightFor(13) + 4, opacity:.5, lineCap:"round"});
    line._d = d;
    line.bindPopup(() =>
      `<div class="pop"><h3>${esc(d.street)}</h3>` +
      `<p class="lim">${esc(d.from || "")}${d.to ? " → " + esc(d.to) : ""}</p>` +
      `<span class="tag" style="background:#0f8a5f">Paving plan</span>` +
      `<dl style="margin-top:8px">` +
      `<dt>Plan</dt><dd>${esc(d.label || "")}</dd>` +
      (d.year ? `<dt>Fiscal year</dt><dd>FY${esc(d.year)}</dd>` : "") +
      (d.detail ? `<dt>Detail</dt><dd>${esc(d.detail)}</dd>` : "") +
      `<dt>City</dt><dd>${esc(d.city)}</dd></dl></div>`, {maxWidth:320});
    return line;
  });

  const moratLines = morat.map(d => {
    const line = L.polyline(latlngs(d._geom),
      {pane:"moratPane", color:"#3f4448", weight:weightFor(13) + 4, opacity:.55,
       dashArray:"1,7", lineCap:"round"});
    line._d = d;
    line.bindPopup(() =>
      `<div class="pop"><h3>${esc(d.street)}</h3>` +
      `<span class="tag" style="background:#3f4448">Paving moratorium</span>` +
      `<dl style="margin-top:8px">` +
      (d.ends ? `<dt>Ends</dt><dd>${esc(d.ends)}${d.active ? "" : " (expired)"}</dd>` : "") +
      (d.from ? `<dt>${d.city === "Oakland" ? "Project" : "From"}</dt><dd>${esc(d.from)}</dd>` : "") +
      (d.to ? `<dt>${d.city === "Oakland" ? "Program" : "To"}</dt><dd>${esc(d.to)}</dd>` : "") +
      `<dt>City</dt><dd>${esc(d.city)}</dd></dl></div>`, {maxWidth:320});
    return line;
  });

  function sectionPopup(d, band){
    const rows = [
      ["Remaining life", d.rsl_years == null ? null : d.rsl_years.toFixed(1) + " yrs"],
      ["Functional class", d.functional_class],
      ["Surface", d.surface_type],
      ["Length", d.length_ft ? num(Math.round(d.length_ft)) + " ft" : null],
      ["Width", d.width_ft ? num(Math.round(d.width_ft)) + " ft" : null],
      ["Surveyed", d.survey_date],
      ["Last treatment", d.last_treatment
        ? esc(d.last_treatment) + (d.last_treatment_date ? " (" + esc(d.last_treatment_date) + ")" : "")
        : null],
      ["Bike facility", d.bike_tier ? (tierOf(d.bike_tier) || {}).label : null],
      ["Section id", d.section_id],
      ["Geometry match", d.match_method]
    ].filter(r => r[1] != null && r[1] !== "");
    return `<div class="pop"><h3>${esc(d.street)}</h3>` +
      `<p class="lim">${esc(d.from || "")} → ${esc(d.to || "")} · ${esc(d.city)}</p>` +
      `<div class="pci"><b style="color:${band.color}">${d.pci}</b>` +
      `<span class="tag" style="background:${band.color}">${esc(band.name)}</span></div>` +
      "<dl>" + rows.map(r => `<dt>${esc(r[0])}</dt><dd>${r[1]}</dd>`).join("") + "</dl></div>";
  }

  const bikeGroup  = L.layerGroup(bikeLines.concat(offLines)).addTo(map);
  const pciGroup   = L.layerGroup(pciLines).addTo(map);
  const planGroup  = L.layerGroup(planLines);
  const moratGroup = L.layerGroup(moratLines);

  map.on("zoomend", () => {
    const w = weightFor(map.getZoom());
    pciLines.forEach(l => l.setStyle({weight:w}));
    bikeLines.concat(offLines).forEach(l => l.setStyle(
      {weight:w * l._tier.mult, dashArray:dashFor(l._tier, w)}));
    planLines.forEach(l => l.setStyle({weight:w + 4}));
    moratLines.forEach(l => l.setStyle({weight:w + 4}));
  });

  // ---- filters -----------------------------------------------------------
  const onBand = new Set(BANDS.map(b => b.name));
  const onCity = new Set(["Berkeley", "Oakland"]);
  const onTier = new Set(TIERS.map(t => t.key));
  let bikeOnly = false;

  function refresh(){
    pciGroup.clearLayers();
    pciLines.forEach(l => {
      if (onBand.has(l._band.name) && onCity.has(l._d.city)
          && (!bikeOnly || l._d.bike_tier)) pciGroup.addLayer(l);
    });
    bikeGroup.clearLayers();
    bikeLines.concat(offLines).forEach(l => {
      if (onTier.has(l._d.tier) && onCity.has(l._d.city)) bikeGroup.addLayer(l);
    });
    [[planGroup, planLines], [moratGroup, moratLines]].forEach(([g, ls]) => {
      if (!map.hasLayer(g)) return;
      g.clearLayers();
      ls.forEach(l => { if (onCity.has(l._d.city)) g.addLayer(l); });
    });
  }

  function toggle(container, items, isOn, onClick, swatchStyle){
    container.innerHTML = "";
    items.forEach(it => {
      const b = document.createElement("button");
      b.className = "row"; b.type = "button";
      b.setAttribute("aria-pressed", String(isOn(it)));
      b.innerHTML = `<span class="swatch" style="${swatchStyle(it)}"></span>` +
                    `<span class="name">${esc(it.label)}` +
                    (it.range ? ` <span class="rng">${it.range}</span>` : "") + `</span>` +
                    `<span class="num">${it.count == null ? "" : num(it.count)}</span>`;
      b.onclick = () => { onClick(it); b.setAttribute("aria-pressed", String(isOn(it))); refresh(); };
      container.appendChild(b);
    });
  }

  const countBand = n => sections.filter(
    d => bandOf(d.pci).name === n && onCity.has(d.city)
         && (!bikeOnly || d.bike_tier)).length;

  function drawLegend(){
    toggle(document.getElementById("legend"),
      BANDS.slice().reverse().map(b => ({
        key:b.name, label:b.name, range:`${b.lo}–${b.hi}`, color:b.color, count:countBand(b.name)
      })),
      it => onBand.has(it.key),
      it => { onBand.has(it.key) ? onBand.delete(it.key) : onBand.add(it.key); },
      it => `background:${it.color}`);
  }

  function drawCities(){
    toggle(document.getElementById("cities"),
      ["Berkeley", "Oakland"].map(c => ({
        key:c, label:c, color:"#52514e",
        count:sections.filter(d => d.city === c).length
      })),
      it => onCity.has(it.key),
      it => { onCity.has(it.key) ? onCity.delete(it.key) : onCity.add(it.key);
              drawLegend(); drawTiers(); drawBikeStat(); },
      () => "background:#52514e");
  }

  toggle(document.getElementById("overlays"),
    [{key:"plan", label:"5-year paving plans", color:"#0f8a5f", group:planGroup, count:plan.length},
     {key:"morat", label:"Paving moratorium", color:"#3f4448", group:moratGroup, count:morat.length}],
    it => map.hasLayer(it.group),
    it => {
      if (map.hasLayer(it.group)) map.removeLayer(it.group);
      else it.group.addTo(map);   // stacking comes from the pane z-index
    },
    it => `background:${it.color}`);

  const tierMiles = key => [...onCity].reduce((a, c) => {
    const b = (DATA.summary.cities[c] || {}).bike_network;
    return a + ((b && b.miles_by_tier && b.miles_by_tier[key]) || 0);
  }, 0);

  function drawTiers(){
    toggle(document.getElementById("biketiers"),
      TIERS.map(t => ({key:t.key, label:t.label, color:BIKE_COLOR, dash:t.dash})),
      it => onTier.has(it.key),
      it => { onTier.has(it.key) ? onTier.delete(it.key) : onTier.add(it.key); },
      it => {
        if (!it.dash) return `background:${it.color};height:7px`;
        const on = it.dash[0] * 2.6, off = it.dash[1] * 2.6;   // legend-scale px
        return `height:7px;background:repeating-linear-gradient(90deg,` +
               `${it.color} 0 ${on}px, transparent ${on}px ${on + off}px)`;
      });
    document.querySelectorAll("#biketiers .num").forEach((el, i) => {
      el.textContent = TIERS[i] ? tierMiles(TIERS[i].key).toFixed(1) + " mi" : "";
    });
  }

  function drawBikeStat(){
    const cities = [...onCity];
    const parts = cities.map(c => {
      const b = (DATA.summary.cities[c] || {}).bike_network;
      if (!b) return null;
      return `${c}: PCI <b>${b.weighted_pci_on_network}</b> on the bike network` +
             ` vs ${b.weighted_pci_citywide} citywide` +
             ` \u00b7 ${b.poor_or_failed_on_network} sections Poor/Failed`;
    }).filter(Boolean);
    document.getElementById("bikestat").innerHTML = parts.join("<br>");
  }

  const bikeBox = document.getElementById("bikeonly");
  bikeBox.addEventListener("change", () => {
    bikeOnly = bikeBox.checked;
    drawLegend();
    refresh();
  });

  drawLegend(); drawCities(); drawTiers(); drawBikeStat();

  // ---- summary tiles (also the required non-colour reading of the data) ---
  const s = DATA.summary.cities;
  document.getElementById("stats").innerHTML = ["Berkeley", "Oakland"].map(c =>
    `<div class="stat"><b>${s[c].weighted_pci_mapped}</b>
     <span>${c} average PCI<br>${num(s[c].sections_mapped)} of ${num(s[c].sections_in_report)} sections mapped
     (${(100 * (1 - s[c].unmatched_rate)).toFixed(1)}%)</span></div>`).join("");
})();
</script>
</body>
</html>
"""


PCI_CREDIT = "PCI: Berkeley P-TAP 25 &amp; Oakland P-TAP 25 (fall 2024)"
OSM_CREDIT = ('&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              " contributors")
TRACESTRACK_CREDIT = (
    'Data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    " contributors, SRTM, GEBCO, SONNY&apos;s LiDAR DTM, NASADEM, ESA WorldCover;"
    ' Maps &copy; <a href="https://tracestrack.com/">Tracestrack</a>')


def tracestrack_key():
    """Key from the environment, else a local .env. Never hard-coded."""
    key = os.environ.get("TRACESTRACK_API_KEY")
    if key:
        return key.strip()
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "..", ".env"), os.path.join(here, ".env")):
        if os.path.exists(path):
            m = re.search(r"^\s*(?:VITE_)?TRACESTRACK_API_KEY\s*=\s*(.+?)\s*$",
                          open(path).read(), re.M)
            if m:
                return m.group(1).strip().strip("\"'")
    return None


def main(payload_path, out_html):
    payload = open(payload_path).read()
    # </script> inside the JSON would close the tag early.
    payload = payload.replace("</", "<\\/")

    key = tracestrack_key()
    if key:
        url = ("https://tile.tracestrack.com/topo__/{z}/{x}/{y}@1x.png?key=" + key)
        attr = TRACESTRACK_CREDIT + " \u00b7 " + PCI_CREDIT
        print("  basemap: Tracestrack topo (key injected \u2014 built file contains a credential)")
    else:
        url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attr = OSM_CREDIT + " \u00b7 " + PCI_CREDIT
        print("  basemap: OpenStreetMap (no TRACESTRACK_API_KEY found)")

    html = (HTML.replace("__PAYLOAD__", payload)
                .replace("__TILE_URL__", json.dumps(url))
                .replace("__TILE_ATTR__", json.dumps(attr)))
    open(out_html, "w").write(html)
    print(f"wrote {out_html} ({len(html)/1e6:.2f} MB)")


if __name__ == "__main__":
    main(*sys.argv[1:3])
