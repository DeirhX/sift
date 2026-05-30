#!/usr/bin/env python3
"""
generate_viewer.py — Generate a self-contained interactive HTML viewer from
                     an audit_report.json produced by photo_audit.py.

Usage:
  python generate_viewer.py <audit_report.json> [--out viewer.html]
"""

import json
import sys
import argparse
from pathlib import Path


def _url(path_str: str) -> str:
    """Convert absolute path to a file:/// URL."""
    try:
        return Path(path_str).as_uri()
    except Exception:
        return path_str


def generate_viewer(report_path: Path, out_path: Path | None = None) -> Path:
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    if out_path is None:
        out_path = report_path.parent / "audit_viewer.html"

    images = report["images"]

    # ── Enrich records with file URLs and image dimensions for face overlays ──
    print(f"Processing {len(images)} image records...")
    from PIL import Image as PILImage

    has_faces   = any(img.get("faces") for img in images)
    has_caption = any(img.get("caption") for img in images)
    has_para    = "para_aesthetic" in (images[0] if images else {})
    has_iqa     = "clip_iqa"       in (images[0] if images else {})

    enriched = []
    for img in images:
        entry = dict(img)
        entry["url"] = _url(img["path"])
        if has_faces and img.get("faces") and not img.get("imgw"):
            try:
                with PILImage.open(img["path"]) as pil:
                    entry["imgw"], entry["imgh"] = pil.size
            except Exception:
                entry["imgw"] = entry["imgh"] = None
        enriched.append(entry)

    # ── Collect metadata for sidebar ──
    people: dict[int, str] = {}   # cluster_id → name (named ones)
    cluster_ids: set[int] = set()
    for img in enriched:
        for face in img.get("faces", []):
            cid = face.get("cluster_id", -1)
            if cid >= 0:
                cluster_ids.add(cid)
            if face.get("name"):
                people[cid] = face["name"]

    tag_counts: dict[str, int] = {}
    for img in enriched:
        for tag in img.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = sorted(tag_counts, key=lambda t: -tag_counts[t])[:40]

    meta = {
        "folder":      report.get("folder", ""),
        "backend":     report.get("backend", ""),
        "total":       len(enriched),
        "dup_groups":  report.get("duplicate_groups", 0),
        "has_para":    has_para,
        "has_iqa":     has_iqa,
        "has_caption": has_caption,
        "has_faces":   has_faces,
        "top_tags":    top_tags,
        "n_clusters":  len(cluster_ids),
        "cluster_names": {str(k): v for k, v in people.items()},
    }

    data_json = json.dumps(enriched,  ensure_ascii=False, separators=(",", ":"))
    meta_json = json.dumps(meta,      ensure_ascii=False, separators=(",", ":"))

    html = _build_html(data_json, meta_json)
    out_path.write_text(html, encoding="utf-8")
    print(f"Viewer saved: {out_path}")
    return out_path


# ─── HTML / CSS / JS template ─────────────────────────────────────────────────

def _build_html(data_json: str, meta_json: str) -> str:
    CLUSTER_COLORS = [
        "#e74c3c","#3498db","#2ecc71","#f39c12","#9b59b6",
        "#1abc9c","#e67e22","#e91e63","#00bcd4","#8bc34a",
        "#ff5722","#607d8b","#ffeb3b","#795548","#7986cb",
    ]
    colors_json = json.dumps(CLUSTER_COLORS)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Photo Audit Viewer</title>
<style>
:root{{
  --bg:#141414;--bg2:#1c1c1c;--bg3:#242424;
  --border:#2e2e2e;--text:#ddd;--muted:#888;
  --accent:#4a9eff;--green:#2ecc71;--red:#e74c3c;--yellow:#f0a500;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:13px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden}}
/* ── Sidebar ── */
#sidebar{{width:230px;flex-shrink:0;background:var(--bg2);border-right:1px solid var(--border);overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:0}}
#sidebar h1{{font-size:13px;font-weight:700;color:var(--text);margin-bottom:2px}}
#sidebar .sub{{font-size:11px;color:var(--muted);margin-bottom:10px}}
.sb-sec{{border-top:1px solid var(--border);padding:8px 0 4px}}
.sb-title{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin-bottom:6px}}
.sb-stat{{font-size:12px;color:var(--muted);margin:1px 0}}
.sb-stat b{{color:var(--text)}}
label.chk{{display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;padding:2px 0}}
label.chk input{{margin:0;accent-color:var(--accent)}}
select,input[type=range]{{width:100%;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:5px;padding:4px 6px;font-size:12px;margin:3px 0}}
input[type=range]{{padding:4px 0}}
.range-vals{{display:flex;justify-content:space-between;font-size:10px;color:var(--muted)}}
.export-btn{{width:100%;padding:5px;background:rgba(74,158,255,.15);color:var(--accent);border:1px solid rgba(74,158,255,.4);border-radius:6px;cursor:pointer;font-size:12px;margin-top:4px}}
.export-btn:hover{{background:rgba(74,158,255,.25)}}
/* ── Main ── */
#main{{flex:1;overflow-y:auto;display:flex;flex-direction:column}}
#statusbar{{position:sticky;top:0;z-index:50;background:rgba(20,20,20,.95);backdrop-filter:blur(6px);border-bottom:1px solid var(--border);padding:5px 12px;font-size:11px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap}}
#statusbar b{{color:var(--text)}}
#grid{{padding:10px;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px}}
/* ── Card ── */
.card{{background:var(--bg3);border-radius:8px;overflow:hidden;border:1.5px solid transparent;transition:border-color .15s}}
.card:hover{{border-color:var(--border)}}
.card.keep{{border-color:var(--green)!important}}
.card.del{{border-color:var(--red)!important;opacity:.55}}
.card.hidden{{display:none}}
/* Image + face overlays */
.img-wrap{{position:relative;background:#0a0a0a;overflow:hidden;cursor:pointer}}
.img-wrap img{{width:100%;display:block;max-height:200px;object-fit:cover;transition:opacity .2s}}
.img-wrap:hover img{{opacity:.88}}
.face-box{{position:absolute;border:2px solid;border-radius:3px;pointer-events:none}}
.face-label{{position:absolute;bottom:calc(100% + 1px);left:-1px;padding:1px 5px;font-size:9px;font-weight:600;white-space:nowrap;border-radius:3px 3px 0 0;color:#fff;pointer-events:auto;cursor:pointer}}
.face-label:hover{{filter:brightness(1.2)}}
/* Card body */
.card-body{{padding:7px 8px 8px}}
.card-hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;gap:4px}}
.fname{{font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}}
.hdr-badges{{display:flex;gap:3px;flex-shrink:0}}
.badge{{font-size:9px;padding:1px 5px;border-radius:8px;font-weight:600}}
.badge-dup{{background:rgba(240,165,0,.2);color:#f0a500}}
.action-wrap{{display:flex;gap:3px}}
.act-btn{{font-size:10px;padding:2px 7px;border-radius:10px;border:none;cursor:pointer;font-weight:600}}
.btn-keep{{background:rgba(46,204,113,.2);color:var(--green)}}
.btn-del{{background:rgba(231,76,60,.2);color:var(--red)}}
.btn-keep.active{{background:rgba(46,204,113,.4)}}
.btn-del.active{{background:rgba(231,76,60,.4)}}
/* Score bars */
.scores{{margin:3px 0}}
.score-row{{display:flex;align-items:center;gap:5px;margin:1.5px 0}}
.sc-lbl{{font-size:10px;color:var(--muted);width:50px;flex-shrink:0}}
.sc-track{{flex:1;height:4px;background:#2a2a2a;border-radius:2px;overflow:hidden}}
.sc-fill{{height:100%;border-radius:2px}}
.sc-val{{font-size:10px;color:var(--muted);width:28px;text-align:right}}
/* Caption */
.caption{{font-size:11px;color:var(--muted);font-style:italic;margin:4px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
/* Tags & People */
.chips{{display:flex;flex-wrap:wrap;gap:3px;margin:3px 0}}
.chip{{font-size:9px;padding:1px 6px;border-radius:8px;background:#2a2a2a;color:#aaa;cursor:pointer}}
.chip:hover{{background:#333}}
.person-chip{{font-size:9px;padding:1px 7px;border-radius:8px;color:#fff;font-weight:600;cursor:pointer}}
.person-chip:hover{{filter:brightness(1.2)}}
/* ── Modal (face naming) ── */
#modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:200;align-items:center;justify-content:center}}
#modal-overlay.open{{display:flex}}
#modal{{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:20px;min-width:300px}}
#modal h3{{margin-bottom:10px;font-size:14px}}
#modal p{{font-size:12px;color:var(--muted);margin-bottom:8px}}
#modal-input{{width:100%;padding:7px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;font-size:14px}}
.modal-btns{{display:flex;gap:8px;margin-top:10px}}
.mbtn{{flex:1;padding:7px;border-radius:6px;border:none;cursor:pointer;font-size:13px;font-weight:600}}
.mbtn-ok{{background:var(--accent);color:#fff}}
.mbtn-cancel{{background:#2a2a2a;color:var(--text)}}
</style>
</head>
<body>

<!-- ══ Sidebar ══════════════════════════════════════════════════════════════ -->
<div id="sidebar">
  <h1>Photo Audit Viewer</h1>
  <div class="sub" id="sb-folder"></div>

  <div class="sb-sec">
    <div class="sb-title">Statistics</div>
    <div class="sb-stat" id="sb-total"></div>
    <div class="sb-stat" id="sb-dups"></div>
    <div class="sb-stat" id="sb-marks"></div>
  </div>

  <div class="sb-sec">
    <div class="sb-title">Sort</div>
    <select id="sort-sel">
      <option value="combined-asc">Combined ↑ (worst first)</option>
      <option value="combined-desc">Combined ↓ (best first)</option>
      <option value="sharpness-asc">Sharpness ↑</option>
      <option value="sharpness-desc">Sharpness ↓</option>
      <option value="para-asc">Aesthetic ↑</option>
      <option value="para-desc">Aesthetic ↓</option>
      <option value="filename-asc">Filename A→Z</option>
    </select>
  </div>

  <div class="sb-sec">
    <div class="sb-title">Combined Score</div>
    <input type="range" id="score-min" min="0" max="1" step="0.01" value="0">
    <input type="range" id="score-max" min="0" max="1" step="0.01" value="1">
    <div class="range-vals"><span id="score-min-lbl">0.00</span><span id="score-max-lbl">1.00</span></div>
  </div>

  <div class="sb-sec">
    <div class="sb-title">Duplicates</div>
    <select id="dup-mode">
      <option value="all">Show all</option>
      <option value="groups-only">Only duplicated images</option>
      <option value="hide-dups">Hide duplicates (keep 1 per group)</option>
      <option value="no-groups">Only unique images</option>
    </select>
  </div>

  <div class="sb-sec" id="people-section" style="display:none">
    <div class="sb-title">People</div>
    <div id="people-filters"></div>
  </div>

  <div class="sb-sec" id="tags-section" style="display:none">
    <div class="sb-title">Tags</div>
    <div id="tag-filters"></div>
  </div>

  <div class="sb-sec">
    <div class="sb-title">Marked</div>
    <label class="chk"><input type="checkbox" id="show-keep"> Show kept only</label>
    <label class="chk"><input type="checkbox" id="show-del"> Show deletions only</label>
    <button class="export-btn" onclick="exportDecisions()">⬇ Export decisions (JSON)</button>
    <button class="export-btn" onclick="exportNames()" id="export-names-btn" style="display:none">⬇ Export face names (JSON)</button>
    <button class="export-btn" onclick="clearAll()" style="background:rgba(231,76,60,.1);color:var(--red);border-color:rgba(231,76,60,.3)">✕ Clear all marks</button>
  </div>
</div>

<!-- ══ Main ════════════════════════════════════════════════════════════════ -->
<div id="main">
  <div id="statusbar">
    <span>Showing <b id="st-shown">–</b> of <b id="st-total">–</b></span>
    <span>Keep: <b id="st-keep" style="color:var(--green)">0</b></span>
    <span>Delete: <b id="st-del" style="color:var(--red)">0</b></span>
    <span>Unmarked: <b id="st-unmarked">–</b></span>
  </div>
  <div id="grid"></div>
</div>

<!-- ══ Face-name modal ═══════════════════════════════════════════════════ -->
<div id="modal-overlay">
  <div id="modal">
    <h3>Name this person</h3>
    <p id="modal-desc"></p>
    <input id="modal-input" type="text" placeholder="Enter name (blank = clear)">
    <div class="modal-btns">
      <button class="mbtn mbtn-ok" onclick="confirmName()">OK</button>
      <button class="mbtn mbtn-cancel" onclick="closeModal()">Cancel</button>
    </div>
  </div>
</div>

<script>
const DATA = {data_json};
const META = {meta_json};
const CLUSTER_COLORS = {colors_json};

// ── State ──────────────────────────────────────────────────────────────────
const LS_MARKS  = 'audit_marks_v1';
const LS_NAMES  = 'audit_names_v1';

let marks = JSON.parse(localStorage.getItem(LS_MARKS) || '{{}}');   // idx → 'keep'|'del'
let clusterNames = Object.assign(
  {{}},
  META.cluster_names || {{}},
  JSON.parse(localStorage.getItem(LS_NAMES) || '{{}}')
);

let filterState = {{
  scoreMin: 0, scoreMax: 1,
  dupMode: 'all',
  people: null,   // null = show all; Set of cluster IDs
  tags: null,     // null = show all; Set of tag strings
  showKeep: false, showDel: false,
  sort: 'combined-asc',
}};

let modalCid = null;  // cluster id being named

// ── Score → color ──────────────────────────────────────────────────────────
function scoreColor(v) {{
  v = Math.max(0, Math.min(1, v));
  if (v < 0.5) {{
    return `rgb(220,${{Math.round(220 * v * 2)}},0)`;
  }} else {{
    return `rgb(${{Math.round(220 * (1-v) * 2)}},200,0)`;
  }}
}}

function clusterColor(cid) {{
  if (cid < 0) return '#555';
  return CLUSTER_COLORS[cid % CLUSTER_COLORS.length];
}}

function clusterLabel(cid) {{
  if (cid < 0) return '?';
  return clusterNames[cid] || `Person ${{String.fromCharCode(65 + (cid % 26))}}`;
}}

// ── Sidebar init ───────────────────────────────────────────────────────────
function initSidebar() {{
  document.getElementById('sb-folder').textContent = META.folder || '';
  document.getElementById('sb-total').innerHTML =
    `Total: <b>${{META.total}}</b> images`;
  document.getElementById('sb-dups').innerHTML =
    `Duplicate groups: <b>${{META.dup_groups}}</b>`;

  // People filters
  if (META.has_faces && META.n_clusters > 0) {{
    const sec = document.getElementById('people-section');
    sec.style.display = '';
    const cont = document.getElementById('people-filters');
    // Collect all cluster IDs that appear in the data
    const cidSet = new Set();
    DATA.forEach(img => (img.faces || []).forEach(f => {{
      if (f.cluster_id >= 0) cidSet.add(f.cluster_id);
    }}));

    document.getElementById('export-names-btn').style.display = '';

    cidSet.forEach(cid => {{
      const lbl = document.createElement('label');
      lbl.className = 'chk';
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = true;
      cb.dataset.cid = cid;
      cb.onchange = updateFilter;
      const dot = document.createElement('span');
      dot.style.cssText = `display:inline-block;width:8px;height:8px;border-radius:50%;background:${{clusterColor(cid)}}`;
      const span = document.createElement('span');
      span.id = `person-lbl-${{cid}}`;
      span.textContent = clusterLabel(cid);
      lbl.append(cb, dot, span);
      cont.appendChild(lbl);
    }});
  }}

  // Tag filters
  if (META.top_tags && META.top_tags.length) {{
    document.getElementById('tags-section').style.display = '';
    const cont = document.getElementById('tag-filters');
    META.top_tags.forEach(tag => {{
      const lbl = document.createElement('label');
      lbl.className = 'chk';
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = false;
      cb.dataset.tag = tag;
      cb.onchange = updateFilter;
      lbl.append(cb, document.createTextNode(tag));
      cont.appendChild(lbl);
    }});
  }}

  // Score sliders
  const smin = document.getElementById('score-min');
  const smax = document.getElementById('score-max');
  smin.oninput = () => {{
    if (+smin.value > +smax.value) smax.value = smin.value;
    document.getElementById('score-min-lbl').textContent = (+smin.value).toFixed(2);
    filterState.scoreMin = +smin.value;
    renderGrid();
  }};
  smax.oninput = () => {{
    if (+smax.value < +smin.value) smin.value = smax.value;
    document.getElementById('score-max-lbl').textContent = (+smax.value).toFixed(2);
    filterState.scoreMax = +smax.value;
    renderGrid();
  }};

  document.getElementById('sort-sel').onchange = e => {{
    filterState.sort = e.target.value;
    renderGrid();
  }};
  document.getElementById('dup-mode').onchange = e => {{
    filterState.dupMode = e.target.value;
    renderGrid();
  }};
  document.getElementById('show-keep').onchange = e => {{
    filterState.showKeep = e.target.checked;
    renderGrid();
  }};
  document.getElementById('show-del').onchange = e => {{
    filterState.showDel = e.target.checked;
    renderGrid();
  }};
}}

function updateFilter() {{
  // People: null = all, else Set of cids that are checked
  const cbs = document.querySelectorAll('#people-filters input[data-cid]');
  const allChecked = [...cbs].every(c => c.checked);
  filterState.people = allChecked ? null : new Set(
    [...cbs].filter(c => c.checked).map(c => +c.dataset.cid)
  );

  // Tags: null = no filter; Set of required tags
  const tcbs = document.querySelectorAll('#tag-filters input[data-tag]');
  const anyChecked = [...tcbs].some(c => c.checked);
  filterState.tags = anyChecked ? new Set(
    [...tcbs].filter(c => c.checked).map(c => c.dataset.tag)
  ) : null;

  renderGrid();
}}

// ── Keep track of which groups have already shown one image ────────────────
let seenGroups = new Set();

function passesFilter(img, idx) {{
  const score = img.combined ?? 0;
  if (score < filterState.scoreMin || score > filterState.scoreMax) return false;

  // Dup mode
  const g = img.dup_group;
  if (filterState.dupMode === 'groups-only' && g == null) return false;
  if (filterState.dupMode === 'no-groups'   && g != null) return false;
  if (filterState.dupMode === 'hide-dups') {{
    if (g != null) {{
      if (seenGroups.has(g)) return false;
      seenGroups.add(g);
    }}
  }}

  // Mark filters
  const m = marks[idx];
  if (filterState.showKeep && m !== 'keep') return false;
  if (filterState.showDel  && m !== 'del')  return false;

  // People filter
  if (filterState.people !== null) {{
    const faceCids = (img.faces || []).map(f => f.cluster_id);
    const hasMatch = faceCids.some(cid => filterState.people.has(cid));
    if (!hasMatch) return false;
  }}

  // Tag filter (OR match — show if any checked tag is present)
  if (filterState.tags !== null) {{
    const imgTags = new Set(img.tags || []);
    const hasTag = [...filterState.tags].some(t => imgTags.has(t));
    if (!hasTag) return false;
  }}

  return true;
}}

// ── Sort ───────────────────────────────────────────────────────────────────
function sortedIndices() {{
  const indices = DATA.map((_, i) => i);
  const [field, dir] = filterState.sort.split('-');
  indices.sort((a, b) => {{
    let va, vb;
    if (field === 'combined')  {{ va = DATA[a].combined  ?? 0; vb = DATA[b].combined  ?? 0; }}
    if (field === 'sharpness') {{ va = DATA[a].sharpness ?? 0; vb = DATA[b].sharpness ?? 0; }}
    if (field === 'para')      {{ va = DATA[a].para_aesthetic ?? DATA[a].clip_iqa ?? 0;
                                  vb = DATA[b].para_aesthetic ?? DATA[b].clip_iqa ?? 0; }}
    if (field === 'filename')  {{ va = DATA[a].filename; vb = DATA[b].filename; }}
    if (dir === 'asc') return va < vb ? -1 : va > vb ? 1 : 0;
    else               return va > vb ? -1 : va < vb ? 1 : 0;
  }});
  return indices;
}}

// ── Render grid ────────────────────────────────────────────────────────────
let cardEls = [];  // pre-built card elements, indexed by DATA index

function buildAllCards() {{
  const frag = document.createDocumentFragment();
  cardEls = DATA.map((img, idx) => {{
    const card = buildCard(img, idx);
    frag.appendChild(card);
    return card;
  }});
  document.getElementById('grid').appendChild(frag);
}}

function renderGrid() {{
  seenGroups = new Set();
  const order = sortedIndices();

  let shown = 0, kept = 0, deleted = 0;
  order.forEach(idx => {{
    const visible = passesFilter(DATA[idx], idx);
    cardEls[idx].classList.toggle('hidden', !visible);
    if (visible) {{
      shown++;
      // move card to correct position in DOM
      document.getElementById('grid').appendChild(cardEls[idx]);
    }}
    const m = marks[idx];
    if (m === 'keep') kept++;
    if (m === 'del')  deleted++;
  }});

  document.getElementById('st-shown').textContent = shown;
  document.getElementById('st-total').textContent = DATA.length;
  document.getElementById('st-keep').textContent  = kept;
  document.getElementById('st-del').textContent   = deleted;
  document.getElementById('st-unmarked').textContent = DATA.length - kept - deleted;
  document.getElementById('sb-marks').innerHTML =
    `Keep: <b style="color:var(--green)">${{kept}}</b> / Delete: <b style="color:var(--red)">${{deleted}}</b>`;
}}

// ── Build a single card ────────────────────────────────────────────────────
function buildCard(img, idx) {{
  const card = document.createElement('div');
  card.className = 'card' + (marks[idx] === 'keep' ? ' keep' : marks[idx] === 'del' ? ' del' : '');
  card.dataset.idx = idx;

  // Image + face overlays
  const wrap = document.createElement('div');
  wrap.className = 'img-wrap';
  wrap.title = img.filename;
  wrap.onclick = () => window.open(img.url, '_blank');

  const imgEl = document.createElement('img');
  imgEl.loading = 'lazy';
  imgEl.src = img.url;
  imgEl.alt = img.filename;
  wrap.appendChild(imgEl);

  // Face overlays (need image dimensions to position as %)
  if (img.faces && img.faces.length && img.imgw && img.imgh) {{
    img.faces.forEach(face => {{
      if (face.cluster_id < 0) return;
      const [x1, y1, x2, y2] = face.bbox;
      const color = clusterColor(face.cluster_id);
      const pct = (v, total) => (v / total * 100).toFixed(2) + '%';

      const box = document.createElement('div');
      box.className = 'face-box';
      box.style.cssText = `
        left:${{pct(x1, img.imgw)}};top:${{pct(y1, img.imgh)}};
        width:${{pct(x2-x1, img.imgw)}};height:${{pct(y2-y1, img.imgh)}};
        border-color:${{color}};
      `;

      const lbl = document.createElement('div');
      lbl.className = 'face-label';
      lbl.style.background = color;
      lbl.textContent = clusterLabel(face.cluster_id);
      lbl.title = 'Click to rename';
      lbl.onclick = (e) => {{
        e.stopPropagation();
        openModal(face.cluster_id);
      }};
      box.appendChild(lbl);
      wrap.appendChild(box);
    }});
  }}

  card.appendChild(wrap);

  // Card body
  const body = document.createElement('div');
  body.className = 'card-body';

  // Header row
  const hdr = document.createElement('div');
  hdr.className = 'card-hdr';

  const fname = document.createElement('div');
  fname.className = 'fname';
  fname.textContent = img.filename;
  hdr.appendChild(fname);

  const badges = document.createElement('div');
  badges.className = 'hdr-badges';
  if (img.dup_group != null) {{
    const b = document.createElement('span');
    b.className = 'badge badge-dup';
    b.title = `Duplicate group ${{img.dup_group}}`;
    b.textContent = `G${{img.dup_group}}`;
    badges.appendChild(b);
  }}
  hdr.appendChild(badges);

  const acts = document.createElement('div');
  acts.className = 'action-wrap';
  const btnK = document.createElement('button');
  btnK.className = 'act-btn btn-keep' + (marks[idx] === 'keep' ? ' active' : '');
  btnK.textContent = '✓';
  btnK.title = 'Keep';
  btnK.onclick = (e) => {{ e.stopPropagation(); toggleMark(idx, 'keep'); }};
  const btnD = document.createElement('button');
  btnD.className = 'act-btn btn-del' + (marks[idx] === 'del' ? ' active' : '');
  btnD.textContent = '✗';
  btnD.title = 'Delete';
  btnD.onclick = (e) => {{ e.stopPropagation(); toggleMark(idx, 'del'); }};
  acts.append(btnK, btnD);
  hdr.appendChild(acts);
  body.appendChild(hdr);

  // Score bars
  const scores = document.createElement('div');
  scores.className = 'scores';

  function scoreRow(label, val) {{
    if (val == null) return;
    const row = document.createElement('div');
    row.className = 'score-row';
    row.innerHTML = `
      <span class="sc-lbl">${{label}}</span>
      <span class="sc-track"><span class="sc-fill" style="width:${{(val*100).toFixed(1)}}%;background:${{scoreColor(val)}}"></span></span>
      <span class="sc-val">${{val.toFixed(3)}}</span>
    `;
    scores.appendChild(row);
  }}

  scoreRow('combined',  img.combined);
  scoreRow('sharpness', img.sharpness);
  if (img.para_aesthetic != null) scoreRow('aesthetic', img.para_aesthetic);
  else if (img.clip_iqa  != null) scoreRow('IQA',       img.clip_iqa);
  if (img.para_composition != null) scoreRow('composit.', img.para_composition);
  if (img.para_light       != null) scoreRow('light',     img.para_light);
  body.appendChild(scores);

  // Caption
  if (img.caption) {{
    const cap = document.createElement('div');
    cap.className = 'caption';
    cap.title = img.caption;
    cap.textContent = img.caption;
    body.appendChild(cap);
  }}

  // Tags
  if (img.tags && img.tags.length) {{
    const chips = document.createElement('div');
    chips.className = 'chips';
    img.tags.slice(0, 6).forEach(tag => {{
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = tag;
      chip.onclick = (e) => {{ e.stopPropagation(); filterByTag(tag); }};
      chips.appendChild(chip);
    }});
    body.appendChild(chips);
  }}

  // People
  if (img.faces && img.faces.length) {{
    const cidsSeen = new Set();
    const chips = document.createElement('div');
    chips.className = 'chips';
    img.faces.forEach(face => {{
      const cid = face.cluster_id;
      if (cid < 0 || cidsSeen.has(cid)) return;
      cidsSeen.add(cid);
      const chip = document.createElement('span');
      chip.className = 'person-chip';
      chip.id = `pchip-${{idx}}-${{cid}}`;
      chip.style.background = clusterColor(cid);
      chip.textContent = clusterLabel(cid);
      chip.title = 'Click to rename';
      chip.onclick = (e) => {{ e.stopPropagation(); openModal(cid); }};
      chips.appendChild(chip);
    }});
    if (chips.children.length) body.appendChild(chips);
  }}

  card.appendChild(body);
  return card;
}}

// ── Mark ───────────────────────────────────────────────────────────────────
function toggleMark(idx, mark) {{
  marks[idx] = marks[idx] === mark ? undefined : mark;
  if (marks[idx] === undefined) delete marks[idx];
  localStorage.setItem(LS_MARKS, JSON.stringify(marks));

  const card = cardEls[idx];
  card.classList.remove('keep', 'del');
  if (marks[idx]) card.classList.add(marks[idx]);

  const btnK = card.querySelector('.btn-keep');
  const btnD = card.querySelector('.btn-del');
  btnK.classList.toggle('active', marks[idx] === 'keep');
  btnD.classList.toggle('active', marks[idx] === 'del');

  renderGrid();  // update status counts
}}

// ── Face naming modal ──────────────────────────────────────────────────────
function openModal(cid) {{
  modalCid = cid;
  document.getElementById('modal-desc').textContent =
    `Cluster ${{cid}} · currently: "${{clusterLabel(cid)}}"`;
  document.getElementById('modal-input').value = clusterNames[cid] || '';
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('modal-input').focus();
}}

function closeModal() {{
  document.getElementById('modal-overlay').classList.remove('open');
  modalCid = null;
}}

function confirmName() {{
  if (modalCid === null) return;
  const name = document.getElementById('modal-input').value.trim();
  if (name) {{
    clusterNames[modalCid] = name;
  }} else {{
    delete clusterNames[modalCid];
  }}
  localStorage.setItem(LS_NAMES, JSON.stringify(clusterNames));
  closeModal();
  rebuildNamed();
}}

document.getElementById('modal-input').onkeydown = e => {{
  if (e.key === 'Enter') confirmName();
  if (e.key === 'Escape') closeModal();
}};
document.getElementById('modal-overlay').onclick = e => {{
  if (e.target === document.getElementById('modal-overlay')) closeModal();
}};

function rebuildNamed() {{
  // Update all face labels and person chips
  document.querySelectorAll('.face-label').forEach(lbl => {{
    const box = lbl.parentElement;
    const wrap = box.parentElement;
    const card = wrap.parentElement;
    const idx = +card.dataset.idx;
    // Find the cluster id from box position (approximation — rebuild full card)
  }});
  // Simpler: rebuild all cards
  document.getElementById('grid').innerHTML = '';
  buildAllCards();
  renderGrid();

  // Update sidebar person labels
  document.querySelectorAll('#people-filters [data-cid]').forEach(cb => {{
    const cid = +cb.dataset.cid;
    const span = document.getElementById(`person-lbl-${{cid}}`);
    if (span) span.textContent = clusterLabel(cid);
  }});
}}

// ── Tag click-to-filter ────────────────────────────────────────────────────
function filterByTag(tag) {{
  const tcbs = document.querySelectorAll('#tag-filters input[data-tag]');
  tcbs.forEach(cb => {{ cb.checked = cb.dataset.tag === tag; }});
  filterState.tags = new Set([tag]);
  renderGrid();
}}

// ── Exports ────────────────────────────────────────────────────────────────
function exportDecisions() {{
  const out = {{
    kept:    [],
    deleted: [],
    unmarked: [],
  }};
  DATA.forEach((img, idx) => {{
    const m = marks[idx];
    const entry = {{ path: img.path, filename: img.filename, combined: img.combined }};
    if (m === 'keep')      out.kept.push(entry);
    else if (m === 'del')  out.deleted.push(entry);
    else                   out.unmarked.push(entry);
  }});
  download('audit_decisions.json', JSON.stringify(out, null, 2));
}}

function exportNames() {{
  download('face_names.json', JSON.stringify(clusterNames, null, 2));
}}

function clearAll() {{
  if (!confirm('Clear all keep/delete marks?')) return;
  marks = {{}};
  localStorage.setItem(LS_MARKS, '{{}}');
  cardEls.forEach(card => {{
    card.classList.remove('keep', 'del');
    card.querySelectorAll('.btn-keep,.btn-del').forEach(b => b.classList.remove('active'));
  }});
  renderGrid();
}}

function download(filename, text) {{
  const a = document.createElement('a');
  a.href = 'data:application/json;charset=utf-8,' + encodeURIComponent(text);
  a.download = filename;
  a.click();
}}

// ── Boot ───────────────────────────────────────────────────────────────────
initSidebar();
buildAllCards();
renderGrid();
</script>
</body>
</html>"""


# ── CLI entry point ────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report", help="Path to audit_report.json")
    ap.add_argument("--out", default=None, help="Output HTML path")
    args = ap.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"Error: {report_path} not found"); sys.exit(1)

    out_path = Path(args.out) if args.out else None
    generate_viewer(report_path, out_path)


if __name__ == "__main__":
    main()
