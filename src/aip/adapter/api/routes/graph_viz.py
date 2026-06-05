"""Standalone graph visualization page at /graph-viz.

Serves a minimal HTML page with Cytoscape.js loaded from CDN.
Data fetched from /api/v1/graph/data.

Acceptable for Phase 2B — full NiceGUI integration is Phase 4.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_GRAPH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AIP Knowledge Graph</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #0f0f0f; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }
#header { padding: 10px 16px; background: #1a1a1a; border-bottom: 1px solid #333; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
#header h1 { font-size: 16px; font-weight: 600; color: #fff; }
.filter-group { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.filter-group label { color: #aaa; }
select, input[type=range] { background: #2a2a2a; color: #e0e0e0; border: 1px solid #444; border-radius: 4px; padding: 3px 6px; font-size: 13px; }
#stats { font-size: 12px; color: #888; margin-left: auto; }
#main { display: flex; flex: 1; overflow: hidden; }
#cy { flex: 1; background: #141414; }
#detail { width: 280px; background: #1a1a1a; border-left: 1px solid #333; padding: 14px; overflow-y: auto; font-size: 13px; display: none; }
#detail h2 { font-size: 14px; color: #fff; margin-bottom: 10px; }
#detail .field { margin-bottom: 8px; }
#detail .field-label { color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
#detail .field-value { color: #e0e0e0; margin-top: 2px; }
#legend { padding: 8px 16px; background: #1a1a1a; border-top: 1px solid #333; display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; }
.legend-item { display: flex; align-items: center; gap: 5px; }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; }
</style>
</head>
<body>
<div id="header">
  <h1>AIP Knowledge Graph</h1>
  <div class="filter-group">
    <label>Min confidence:</label>
    <input type="range" id="conf-slider" min="0" max="1" step="0.05" value="0.4">
    <span id="conf-val">0.40</span>
  </div>
  <div class="filter-group">
    <label>Domain:</label>
    <select id="domain-filter"><option value="">All domains</option></select>
  </div>
  <div class="filter-group">
    <label>Type:</label>
    <select id="type-filter">
      <option value="">All types</option>
      <option value="PERSON">Person</option>
      <option value="PROJECT">Project</option>
      <option value="CONCEPT">Concept</option>
      <option value="PLACE">Place</option>
      <option value="ORGANIZATION">Organization</option>
      <option value="MANUSCRIPT">Manuscript</option>
      <option value="DOMAIN">Domain</option>
    </select>
  </div>
  <span id="stats">Loading...</span>
</div>
<div id="main">
  <div id="cy"></div>
  <div id="detail">
    <h2 id="detail-title">Node Detail</h2>
    <div id="detail-body"></div>
  </div>
</div>
<div id="legend">
  <div class="legend-item"><div class="legend-dot" style="background:#4A90D9"></div>Person</div>
  <div class="legend-item"><div class="legend-dot" style="background:#F5A623"></div>Project</div>
  <div class="legend-item"><div class="legend-dot" style="background:#7ED321"></div>Concept</div>
  <div class="legend-item"><div class="legend-dot" style="background:#9B59B6"></div>Place</div>
  <div class="legend-item"><div class="legend-dot" style="background:#E74C3C"></div>Organization</div>
  <div class="legend-item"><div class="legend-dot" style="background:#1ABC9C"></div>Manuscript</div>
  <div class="legend-item"><div class="legend-dot" style="background:#95A5A6"></div>Domain</div>
</div>
<script>
const NODE_COLORS = {
  PERSON: '#4A90D9', PROJECT: '#F5A623', CONCEPT: '#7ED321',
  PLACE: '#9B59B6', ORGANIZATION: '#E74C3C', MANUSCRIPT: '#1ABC9C',
  DOMAIN: '#95A5A6',
};

let cy = null;
let allData = null;

function nodeColor(type) { return NODE_COLORS[type] || '#888888'; }

function buildElements(data, minConf, domainFilter, typeFilter) {
  const nodeIds = new Set();
  const nodes = [];
  for (const n of data.nodes) {
    if (n.confidence < minConf) continue;
    if (domainFilter && n.domain !== domainFilter) continue;
    if (typeFilter && n.entity_type !== typeFilter) continue;
    nodeIds.add(n.id);
    nodes.push({
      data: { id: n.id, label: n.label, entity_type: n.entity_type,
              domain: n.domain, confidence: n.confidence, source: n.source },
    });
  }
  const edges = [];
  for (const e of data.edges) {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) continue;
    if (e.confidence < minConf) continue;
    edges.push({
      data: { id: e.id, source: e.source, target: e.target,
              relationship_type: e.relationship_type, bridge_tag: e.bridge_tag,
              confidence: e.confidence, weight: e.weight },
    });
  }
  return [...nodes, ...edges];
}

function renderGraph(data, minConf, domainFilter, typeFilter) {
  const elements = buildElements(data, minConf, domainFilter, typeFilter);
  document.getElementById('stats').textContent =
    `${elements.filter(e => !e.data.source || e.data.label).length} nodes, ` +
    `${elements.filter(e => e.data.source && !e.data.label).length} edges`;

  if (cy) cy.destroy();
  cy = cytoscape({
    container: document.getElementById('cy'),
    elements,
    style: [
      { selector: 'node', style: {
        'background-color': (ele) => nodeColor(ele.data('entity_type')),
        'label': 'data(label)', 'color': '#fff', 'font-size': '11px',
        'text-outline-color': '#1a1a1a', 'text-outline-width': '2px',
        'width': (ele) => Math.max(20, 20 + ele.degree() * 4),
        'height': (ele) => Math.max(20, 20 + ele.degree() * 4),
        'opacity': (ele) => 0.5 + ele.data('confidence') * 0.5,
      }},
      { selector: 'edge', style: {
        'line-color': '#555', 'target-arrow-color': '#555',
        'target-arrow-shape': 'triangle', 'curve-style': 'bezier',
        'width': (ele) => Math.max(1, Math.min(5, ele.data('weight') || 1)),
        'opacity': (ele) => 0.3 + ele.data('confidence') * 0.5,
      }},
      { selector: ':selected', style: { 'border-width': 3, 'border-color': '#fff' }},
    ],
    layout: { name: 'cose', animate: false, randomize: true, nodeRepulsion: 8000,
              idealEdgeLength: 100, gravity: 0.3 },
  });

  cy.on('tap', 'node', (evt) => {
    const n = evt.target.data();
    document.getElementById('detail').style.display = 'block';
    document.getElementById('detail-title').textContent = n.label;
    document.getElementById('detail-body').innerHTML = `
      <div class="field"><div class="field-label">ID</div><div class="field-value">${n.id}</div></div>
      <div class="field"><div class="field-label">Type</div><div class="field-value">${n.entity_type}</div></div>
      <div class="field"><div class="field-label">Domain</div><div class="field-value">${n.domain || '—'}</div></div>
      <div class="field"><div class="field-label">Confidence</div><div class="field-value">${n.confidence.toFixed(2)}</div></div>
      <div class="field"><div class="field-label">Source</div><div class="field-value">${n.source}</div></div>
      <div class="field"><div class="field-label">Connections</div><div class="field-value">${evt.target.degree()}</div></div>
    `;
  });

  cy.on('tap', (evt) => { if (evt.target === cy) document.getElementById('detail').style.display = 'none'; });
}

async function loadAndRender() {
  const minConf = parseFloat(document.getElementById('conf-slider').value);
  const domainFilter = document.getElementById('domain-filter').value;
  const typeFilter = document.getElementById('type-filter').value;

  if (!allData) {
    try {
      document.getElementById('stats').textContent = 'Loading...';
      const resp = await fetch('/api/v1/graph/data?min_confidence=0');
      allData = await resp.json();
      // Populate domain filter
      const domains = [...new Set(allData.nodes.map(n => n.domain).filter(Boolean))].sort();
      const sel = document.getElementById('domain-filter');
      domains.forEach(d => { const o = document.createElement('option'); o.value = d; o.textContent = d; sel.appendChild(o); });
    } catch (e) {
      document.getElementById('stats').textContent = 'Error loading graph data';
      return;
    }
  }
  renderGraph(allData, minConf, domainFilter, typeFilter);
}

document.getElementById('conf-slider').addEventListener('input', function() {
  document.getElementById('conf-val').textContent = parseFloat(this.value).toFixed(2);
  if (allData) renderGraph(allData, parseFloat(this.value),
    document.getElementById('domain-filter').value,
    document.getElementById('type-filter').value);
});
document.getElementById('domain-filter').addEventListener('change', loadAndRender);
document.getElementById('type-filter').addEventListener('change', loadAndRender);

loadAndRender();
</script>
</body>
</html>
"""


@router.get("/graph-viz", response_class=HTMLResponse)
async def graph_viz():
    """Standalone Cytoscape.js knowledge graph visualization page."""
    return HTMLResponse(content=_GRAPH_HTML)
