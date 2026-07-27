import { Map as MLMap, NavigationControl, ScaleControl, addProtocol,
         setWorkerCount, getWorkerCount, prewarm }
  from "./vendor/maplibre-gl.mjs";
import { Protocol } from "./vendor/pmtiles.mjs";
import { TILES_URL } from "./config.js";

const METRICS = {
  pop_pct:    { label: "Population %",  unit: "%",      pct: true,  noun: "population growth" },
  hu_pct:     { label: "Housing %",     unit: "%",      pct: true,  noun: "housing-unit growth" },
  pop_change: { label: "People added",  unit: " people", pct: false, noun: "people added" },
  hu_change:  { label: "Units added",   unit: " units",  pct: false, noun: "housing units added" },
};

// Breaks are hand-set rather than quantile-derived so the classes mean the same
// thing whatever the filter shows - a ZCTA does not change color when the state
// filter changes. Percentage breaks bracket the national median (~3%).
// Eight cuts -> nine classes, one per ramp step, symmetric about a neutral
// middle class so the gray band always means "essentially flat".
const BREAKS = {
  pct:    [-25, -10, -2, 2, 10, 25, 50, 100],
  people: [-5000, -1500, -250, 250, 1500, 5000, 15000, 30000],
  units:  [-2000, -600, -100, 100, 600, 2000, 6000, 12000],
};

const state = {
  metric: "pop_pct",
  minPop: 1000,
  st: "",
  hideRecut: true,
  selected: null,
  basemap: true,
};

const $ = (s) => document.querySelector(s);

// One source of truth for color. The GL style cannot read CSS custom
// properties, and getComputedStyle at style-build time was picking up whichever
// theme happened to be resolved - so the palette lives here and is pushed into
// CSS for the panel, rather than read back out of it.
const PALETTE = {
  light: {
    ramp: ["#184f95", "#2a78d6", "#86b6ef", "#cde2fb", "#f0efec",
           "#fed4d0", "#ec9991", "#c34c48", "#823430"],
    nodata: "#dedcd5", plane: "#f9f9f7", ink: "#0b0b0b",
    rule: "#c3c2b7", border: "rgba(11,11,11,0.35)",
    basemap: "rastertiles/voyager",
  },
  dark: {
    ramp: ["#85baff", "#4487db", "#2c5991", "#1f344e", "#383835",
           "#4b2724", "#893d39", "#cf5e58", "#f89a92"],
    nodata: "#2c2c2a", plane: "#0d0d0d", ink: "#ffffff",
    rule: "#383835", border: "rgba(255,255,255,0.35)",
    basemap: "dark_all",
  },
};
// The theme is stamped onto <html> at boot rather than left implicit, so the
// CSS panel and the GL map can never disagree about which mode is active
// (browsers that force-darken pages otherwise flip one but not the other).
const store = {
  // Safari in private browsing throws on localStorage access rather than
  // returning null, and a theme preference is not worth taking the app down for.
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

function initTheme() {
  document.documentElement.dataset.theme =
    store.get("zcta-theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
const theme = () => document.documentElement.dataset.theme || "light";
const pal = () => PALETTE[theme()];

const fmt = (n) => (n == null ? "-" : Math.round(n).toLocaleString());
const fmtPct = (n) => (n == null ? "-" : `${n > 0 ? "+" : ""}${n.toFixed(1)}%`);
const isPct = () => METRICS[state.metric].pct;
const breaks = () =>
  isPct() ? BREAKS.pct : state.metric === "pop_change" ? BREAKS.people : BREAKS.units;
const fmtVal = (v) =>
  v == null ? "-" : isPct() ? fmtPct(v) : `${v > 0 ? "+" : ""}${fmt(v)}`;

let summary, map;
// zcta -> full record, from the sidecar. Empty until it lands; every reader
// below tolerates that, so the map is interactive before it arrives.
let byZcta = {};
let ranked = [];

/* ---------- map style ---------- */

// Basemap follows the theme, or the choropleth sits on a surface fighting it.
const carto = () => ({
  type: "raster",
  tiles: ["a", "b", "c"].map(
    (s) =>
      `https://${s}.basemaps.cartocdn.com/${pal().basemap}/{z}/{x}/{y}{ratio}.png`
  ),
  tileSize: 256,
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, &copy; <a href="https://carto.com/attributions">CARTO</a>',
});

function fillColor() {
  // step() over the active metric, transparent where the metric is missing.
  const b = breaks();
  const ramp = pal().ramp;
  const expr = ["step", ["to-number", ["get", state.metric], -1e9], ramp[0]];
  b.forEach((cut, i) => expr.push(cut, ramp[i + 1]));
  // A ZCTA with no estimate for this metric is drawn flat, not as "big decline".
  return ["case", ["==", ["get", state.metric], null], pal().nodata, expr];
}

// Filters are expressed as PAINT, not as layer filters. Changing a layer
// filter makes MapLibre re-parse every loaded tile from raw vector data, which
// is what made the sliders and checkboxes stutter; changing a data-driven paint
// property only re-evaluates attributes. Same visual result, no re-tessellation.
function visibleExpr() {
  const f = ["all", [">=", ["coalesce", ["get", "pop_2024"], 0], state.minPop]];
  if (state.hideRecut) f.push(["!", ["get", "boundary_changed"]]);
  if (state.st) f.push(["==", ["get", "state"], state.st]);
  // Housing metrics carry their own small-base guard.
  if (state.metric.startsWith("hu")) f.push(["get", "comparable_hu"]);
  return f;
}

// The JS mirror of visibleExpr, so hover ignores what the map is hiding -
// zeroed-out opacity still generates pointer events.
function passesFilter(rec) {
  if (!rec) return false;
  if ((rec.pop_2024 ?? 0) < state.minPop) return false;
  if (state.hideRecut && rec.boundary_changed) return false;
  if (state.st && rec.state !== state.st) return false;
  if (state.metric.startsWith("hu") && !rec.comparable_hu) return false;
  return true;
}

const fillOpacity = () => [
  "case", visibleExpr(), state.basemap ? 0.82 : 0.95, 0,
];

let paintQueued = false;
function applyPaint() {
  // The range slider fires continuously; one repaint per frame is plenty.
  if (paintQueued) return;
  paintQueued = true;
  requestAnimationFrame(() => {
    paintQueued = false;
    paintNow();
  });
}

function paintNow() {
  if (!map || !map.getLayer("zcta-fill")) return;
  map.setPaintProperty("zcta-fill", "fill-color", fillColor());
  map.setPaintProperty("zcta-fill", "fill-opacity", fillOpacity());
  map.setPaintProperty("zcta-line", "line-opacity", ["case", visibleExpr(), 1, 0]);
}

function buildStyle() {
  const sources = {
    zctas: {
      type: "vector",
      url: `pmtiles://${TILES_URL}`,
      // Lets hover/selection ride on feature-state instead of a filter swap.
      promoteId: "zcta",
      attribution: "U.S. Census Bureau ACS 5-year, 2020 ZCTAs",
    },
    states: { type: "geojson", data: "data/states.geojson" },
  };
  const layers = [
    { id: "bg", type: "background", paint: { "background-color": pal().plane } },
  ];
  if (state.basemap) {
    sources.carto = carto();
    layers.push({ id: "carto", type: "raster", source: "carto", paint: { "raster-opacity": 1 } });
  }
  layers.push(
    {
      id: "zcta-fill",
      type: "fill",
      source: "zctas",
      "source-layer": "zctas",
      paint: {
        "fill-color": fillColor(),
        "fill-opacity": fillOpacity(),
        // 30k polygons per view: the per-polygon antialias pass is pure cost
        // here, since neighbours share edges and the basemap carries the detail.
        "fill-antialias": false,
      },
    },
    {
      id: "zcta-line",
      type: "line",
      source: "zctas",
      "source-layer": "zctas",
      minzoom: 8,
      paint: {
        "line-color": pal().border,
        "line-width": 0.5,
        "line-opacity": ["case", visibleExpr(), 1, 0],
      },
    },
    {
      id: "zcta-hover",
      type: "line",
      source: "zctas",
      "source-layer": "zctas",
      paint: {
        "line-color": pal().ink,
        "line-width": [
          "case",
          ["boolean", ["feature-state", "selected"], false], 2,
          ["boolean", ["feature-state", "hover"], false], 1.5,
          0,
        ],
      },
    },
    {
      id: "state-line",
      type: "line",
      source: "states",
      paint: { "line-color": pal().rule, "line-width": 0.8, "line-opacity": 0.9 },
    }
  );
  return { version: 8, sources, layers };
}

/* ---------- panel rendering ---------- */

function renderTiles() {
  const n = summary.national;
  const c = summary.counts;
  const growth = ((n.pop_2024 / n.pop_2011 - 1) * 100).toFixed(1);
  const tiles = [
    [`+${growth}%`, "U.S. population, 2011→2024"],
    [`${(summary.national.share_growing * 100).toFixed(0)}%`, "of ZCTAs grew at all"],
    [fmt(n.pop_2024 - n.pop_2011), "people added, net"],
    [fmt(c.ranked), "ZCTAs ranked"],
  ];
  $("#tiles").innerHTML = tiles
    .map(([v, k]) => `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`)
    .join("");
}

function renderLegend() {
  const b = breaks();
  const rows = pal().ramp.map((v, i) => {
    const lo = i === 0 ? null : b[i - 1];
    const hi = i === pal().ramp.length - 1 ? null : b[i];
    const t =
      lo == null ? `below ${fmtVal(hi)}`
      : hi == null ? `${fmtVal(lo)} or more`
      : `${fmtVal(lo)} to ${fmtVal(hi)}`;
    return `<div class="row"><span class="sw" style="background:${v}"></span>${t}</div>`;
  });
  rows.push(
    `<div class="row"><span class="sw" style="background:${pal().nodata}"></span>no estimate</div>`
  );
  $("#legend").innerHTML = rows.join("");
  $("#legend-note").textContent = `Shading shows ${METRICS[state.metric].noun} between the
    ACS 2007–2011 and 2020–2024 five-year estimates.`;
}

// The ranking answers the question the filters are currently asking, so it is
// computed from the full rankable table rather than sifting a fixed national list.
function renderTop() {
  const m = state.metric;
  const rows = ranked
    .filter(
      (r) =>
        r[m] != null &&
        r.pop_2024 >= state.minPop &&
        (!state.st || r.state === state.st) &&
        (!m.startsWith("hu") || r.comparable_hu)
    )
    .sort((a, b) => b[m] - a[m])
    .slice(0, 100);
  $("#top-metric").textContent = `by ${METRICS[state.metric].noun}`;
  $("#top").innerHTML = rows
    .map(
      (r, i) => `<li data-zcta="${r.zcta}" class="${state.selected === r.zcta ? "on" : ""}">
        <span class="rank">${i + 1}</span>
        <span class="name"><b>${r.zcta}</b> <span>${r.label ?? ""}</span></span>
        <span class="val">${fmtVal(r[state.metric])}</span>
      </li>`
    )
    .join("");
}

/* ---------- interaction ---------- */

function detailHTML(zcta) {
  const p = byZcta[zcta];
  if (!p) return `<b>${zcta}</b><div class="r"><span>loading detail…</span></div>`;
  const flags = [];
  if (p.boundary_changed)
    flags.push("Boundary redrawn between the 2010 and 2020 ZCTA vintages - the two endpoints do not cover the same ground.");
  if (p.pop_2024 < 1000) flags.push("Small population: the estimate is noisy.");
  return `<b>${p.zcta}</b> &middot; ${p.label ?? ""}
    <div class="r"><span>Population</span><i>${fmt(p.pop_2011)} → ${fmt(p.pop_2024)}</i></div>
    <div class="r"><span>Change</span><i>${fmtPct(p.pop_pct)} (${p.pop_change > 0 ? "+" : ""}${fmt(p.pop_change)})</i></div>
    <div class="r"><span>Housing units</span><i>${fmt(p.housing_units_2011)} → ${fmt(p.housing_units_2024)}</i></div>
    <div class="r"><span>Change</span><i>${fmtPct(p.hu_pct)}</i></div>
    <div class="r"><span>Density 2024</span><i>${fmt(p.density_2024)}/sq mi</i></div>
    ${flags.map((f) => `<div class="flag">${f}</div>`).join("")}`;
}

let hoverId = null;

function setHover(id) {
  if (hoverId === id) return;
  const src = { source: "zctas", sourceLayer: "zctas" };
  if (hoverId != null) map.setFeatureState({ ...src, id: hoverId }, { hover: false });
  hoverId = id;
  if (id != null) map.setFeatureState({ ...src, id }, { hover: true });
}

function setSelected(id) {
  const src = { source: "zctas", sourceLayer: "zctas" };
  if (state.selected != null)
    map.setFeatureState({ ...src, id: state.selected }, { selected: false });
  state.selected = id;
  if (id != null) map.setFeatureState({ ...src, id }, { selected: true });
}

function wireMap() {
  const tip = $("#tooltip");

  map.on("mousemove", "zcta-fill", (e) => {
    const p = e.features[0].properties;
    // Hidden features still emit pointer events, so re-check the filter here.
    if (!passesFilter(byZcta[p.zcta] ?? p)) {
      setHover(null);
      tip.hidden = true;
      map.getCanvas().style.cursor = "";
      return;
    }
    map.getCanvas().style.cursor = "pointer";
    setHover(p.zcta);
    tip.hidden = false;
    tip.style.left = `${e.point.x}px`;
    tip.style.top = `${e.point.y}px`;
    tip.innerHTML = `<b>${p.zcta}</b> ${byZcta[p.zcta]?.label ?? ""}
      <div class="r"><span>${METRICS[state.metric].noun}</span><i>${fmtVal(
        p[state.metric] == null ? null : Number(p[state.metric])
      )}</i></div>
      <div class="r"><span>Population 2024</span><i>${fmt(p.pop_2024)}</i></div>`;
  });
  map.on("mouseleave", "zcta-fill", () => {
    map.getCanvas().style.cursor = "";
    setHover(null);
    tip.hidden = true;
  });
  map.on("click", "zcta-fill", (e) => {
    const p = e.features[0].properties;
    if (!passesFilter(byZcta[p.zcta] ?? p)) return;
    setSelected(p.zcta);
    $("#detail").hidden = false;
    $("#detail").innerHTML = `<button id="close-detail">&times;</button>${detailHTML(p.zcta)}`;
    $("#close-detail").onclick = () => ($("#detail").hidden = true);
    renderTop();
  });
}

// DOM handlers. Attached before the map exists, so the panel stays usable even
// if WebGL is unavailable; every map call below is guarded.
function wireUI() {
  $("#metric").onclick = (e) => {
    const b = e.target.closest("button[data-metric]");
    if (!b) return;
    state.metric = b.dataset.metric;
    [...$("#metric").children].forEach((c) =>
      c.setAttribute("aria-checked", String(c === b))
    );
    applyPaint();
    renderLegend();
    renderTop();
  };

  $("#minpop").oninput = (e) => {
    state.minPop = +e.target.value;
    $("#minpop-out").textContent = state.minPop.toLocaleString();
    applyPaint();
    renderTop();
  };
  $("#state").onchange = (e) => {
    state.st = e.target.value;
    applyPaint();
    renderTop();
    if (state.st) {
      const pts = Object.values(byZcta).filter((r) => r.state === state.st);
      if (pts.length) {
        const b = pts.reduce(
          (a, r) => [Math.min(a[0], r.lon), Math.min(a[1], r.lat),
                     Math.max(a[2], r.lon), Math.max(a[3], r.lat)],
          [180, 90, -180, -90]
        );
        map?.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 700 });
      }
    }
  };
  $("#hide-recut").onchange = (e) => {
    state.hideRecut = e.target.checked;
    applyPaint();
  };
  $("#basemap").onchange = (e) => {
    state.basemap = e.target.checked;
    if (!map) return;
    // Add/remove just the raster layer. setStyle() would rebuild the whole
    // style and re-parse every vector tile for a basemap checkbox.
    if (state.basemap) {
      if (!map.getSource("carto")) map.addSource("carto", carto());
      if (!map.getLayer("carto"))
        map.addLayer({ id: "carto", type: "raster", source: "carto" }, "zcta-fill");
    } else {
      if (map.getLayer("carto")) map.removeLayer("carto");
      if (map.getSource("carto")) map.removeSource("carto");
    }
    applyPaint();
  };

  $("#top").onclick = (e) => {
    const li = e.target.closest("li[data-zcta]");
    if (!li) return;
    flyTo(li.dataset.zcta);
  };
  $("#search").onchange = (e) => {
    const z = e.target.value.trim().padStart(5, "0");
    if (byZcta[z]) flyTo(z);
    else e.target.setCustomValidity?.("");
  };

  $("#theme").onclick = () => {
    const next = theme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("zcta-theme", next);
    renderLegend();
    if (!map) return;
    // Recolor in place, same reason as the basemap toggle: repainting is cheap,
    // rebuilding the style is not.
    map.setPaintProperty("bg", "background-color", pal().plane);
    map.setPaintProperty("zcta-line", "line-color", pal().border);
    map.setPaintProperty("zcta-hover", "line-color", pal().ink);
    map.setPaintProperty("state-line", "line-color", pal().rule);
    map.getSource("carto")?.setTiles(carto().tiles);
    applyPaint();
  };
}

function flyTo(z) {
  const r = byZcta[z];
  if (!r || !map) return;
  setSelected(z);
  map.flyTo({ center: [r.lon, r.lat], zoom: 10.5, duration: 1200 });
  renderTop();
  $("#detail").hidden = false;
  $("#detail").innerHTML = `<button id="close-detail">&times;</button>${detailHTML(z)}`;
  $("#close-detail").onclick = () => ($("#detail").hidden = true);
}

/* ---------- boot ---------- */

async function loadSidecar() {
  const d = await fetch("data/zctas.json").then((r) => r.json());
  const idx = Object.fromEntries(d.cols.map((c, i) => [c, i]));
  byZcta = {};
  ranked = [];
  for (const row of d.rows) {
    const rec = {};
    for (const c of d.cols) rec[c] = row[idx[c]];
    byZcta[rec.zcta] = rec;
    if (rec.comparable && !rec.boundary_changed) ranked.push(rec);
  }
  renderTop();
  $("#search").disabled = false;
}


(async function init() {
  initTheme();
  addProtocol("pmtiles", new Protocol().tile);

  // Tile parsing is the bottleneck when zooming; the default worker count is
  // conservative. Spin the workers up before the first tile arrives, too.
  const cores = navigator.hardwareConcurrency || 4;
  setWorkerCount(Math.max(getWorkerCount(), Math.min(cores - 1, 8)));
  prewarm();
  summary = await fetch("data/summary.json").then((r) => r.json());

  $("#state").insertAdjacentHTML(
    "beforeend",
    summary.states.map((s) => `<option value="${s}">${s}</option>`).join("")
  );

  // Filters can be set from the query string, so a filtered view is a link you
  // can send someone: ?state=TX&metric=hu_pct&minpop=5000
  const q = new URLSearchParams(location.search);
  if (METRICS[q.get("metric")]) state.metric = q.get("metric");
  if (q.has("minpop")) state.minPop = Math.max(0, +q.get("minpop") || 0);
  if (summary.states.includes(q.get("state"))) state.st = q.get("state");
  if (q.get("recut") === "show") state.hideRecut = false;

  $("#state").value = state.st;
  $("#minpop").value = state.minPop;
  $("#minpop-out").textContent = state.minPop.toLocaleString();
  $("#hide-recut").checked = state.hideRecut;
  [...$("#metric").children].forEach((c) =>
    c.setAttribute("aria-checked", String(c.dataset.metric === state.metric))
  );

  // The panel is useful on its own, so it renders before (and independently of)
  // the GL map - a browser without WebGL2 still gets the numbers and rankings.
  renderTiles();
  renderLegend();
  renderTop();
  wireUI();

  // Kicked off before the map so it still runs if the map cannot be created,
  // but not awaited, so it never delays the first paint.
  loadSidecar();

  try {
    map = new MLMap({
      container: "map",
      style: buildStyle(),
      center: [-97.5, 39],
      zoom: 3.8,
      minZoom: 3,
      maxZoom: 13,
      hash: true,
      // Smoothness settings, all about work avoided per frame:
      fadeDuration: 0,          // no cross-fade re-render while zooming
      renderWorldCopies: false, // one copy of the world, not three
      refreshExpiredTiles: false,
      antialias: false,
      maxTileCacheSize: 800,    // keep tiles across zoom in/out instead of refetching
      collectResourceTiming: false,
    });
  } catch (err) {
    $("#map").innerHTML =
      `<p class="gl-error">This browser can't render the map: ${err.message}</p>`;
    return;
  }
  map.addControl(new NavigationControl({ showCompass: false }), "top-left");
  map.addControl(new ScaleControl({ unit: "imperial" }), "bottom-left");

  // With tiles on object storage, a bad URL or missing CORS is the likeliest
  // deployment failure - say so on screen instead of leaving a blank map.
  map.on("error", (e) => {
    const msg = e.error?.message || String(e);
    console.error("map error:", msg);
    if (e.sourceId === "zctas" && !$("#tile-error")) {
      $("#map-wrap").insertAdjacentHTML(
        "beforeend",
        `<div id="tile-error" class="tile-error">Could not load map tiles from
         <code>${TILES_URL}</code>. If they are on object storage, check the URL
         and that the bucket allows cross-origin GETs with the Range header.</div>`
      );
    }
  });
  map.on("load", wireMap);
})();
