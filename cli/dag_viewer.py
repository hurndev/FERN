from __future__ import annotations

import http.server
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _query_db(db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        events: list[dict[str, Any]] = []
        for row in conn.execute("SELECT * FROM events ORDER BY ts, id"):
            events.append({
                "id": row["id"],
                "type": row["type"],
                "group": row["group_pubkey"],
                "author": row["author"],
                "parents": json.loads(row["parents_json"]),
                "content": json.loads(row["content_json"]),
                "ts": row["ts"],
                "tags": json.loads(row["tags_json"]),
                "sig": row["sig"],
            })

        edges: list[dict[str, str]] = []
        for row in conn.execute("SELECT parent_id, child_id FROM parent_refs"):
            edges.append({"from": row["parent_id"], "to": row["child_id"]})

        groups: dict[str, dict[str, int]] = {}
        for row in conn.execute("SELECT DISTINCT group_pubkey FROM events"):
            gp = row["group_pubkey"]
            groups[gp] = {
                "count": conn.execute(
                    "SELECT COUNT(*) FROM events WHERE group_pubkey = ?", (gp,)
                ).fetchone()[0],
            }

        event_receipts: list[dict[str, Any]] = []
        try:
            for row in conn.execute("SELECT event_id, relay_pubkey, event_receipt_json FROM event_receipts"):
                event_receipts.append({
                    "event_id": row["event_id"],
                    "relay_pubkey": row["relay_pubkey"],
                    "event_receipt": json.loads(row["event_receipt_json"]),
                })
        except sqlite3.OperationalError:
            pass

        return {"events": events, "edges": edges, "groups": groups, "event_receipts": event_receipts}
    finally:
        conn.close()


_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FERN DAG Viewer</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
:root {
  --bg: #fafafa;
  --surface: #ffffff;
  --border: #e0e0e0;
  --border-light: #eee;
  --text: #222;
  --text-dim: #555;
  --text-muted: #888;
  --accent: #2196f3;
  --accent-light: #e3f2fd;
  --danger: #e74c3c;
  --green: #4caf50;
  --yellow: #ff9800;
  --selected: #fff8e0;
  --selected-border: #ffc107;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  font-size: 14px; background: var(--bg); color: var(--text);
  overflow: hidden;
}

/* Top bar */
#topbar {
  display: flex; align-items: center; gap: 16px;
  padding: 0 16px; height: 52px;
  background: var(--surface); border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
#topbar h1 {
  font-size: 17px; font-weight: 700; color: var(--text); white-space: nowrap;
}
#topbar .stats { font-size: 12px; color: var(--text-muted); white-space: nowrap; }
#topbar .spacer { flex: 1; }
#topbar .control-group { display: flex; align-items: center; gap: 6px; }
#topbar label { font-size: 12px; color: var(--text-dim); }
#topbar select, #topbar input[type=text] {
  font: inherit; font-size: 13px; padding: 4px 8px;
  border: 1px solid var(--border); border-radius: 4px;
  background: var(--surface); color: var(--text); outline: none;
}
#topbar select:focus, #topbar input:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-light); }
#topbar input[type=text] { width: 140px; }

/* Main layout */
#app { display: flex; height: calc(100vh - 52px); }
#network-wrap { flex: 1; position: relative; overflow: hidden; background: var(--bg); }
#network { width: 100%; height: 100%; }

/* Floating legend */
#legend {
  position: absolute; bottom: 12px; left: 12px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px;
  font-size: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  max-width: 260px;
}
#legend h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); margin-bottom: 6px; }
#legend .legend-row { display: flex; align-items: center; gap: 8px; margin: 3px 0; }
#legend .legend-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
#legend .legend-star { width: 14px; height: 14px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; font-size: 14px; }

/* Floating toolbar */
#toolbar {
  position: absolute; top: 12px; right: 12px;
  display: flex; gap: 6px;
}
#toolbar button {
  font: inherit; font-size: 12px; font-weight: 500; padding: 6px 12px;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--surface); color: var(--text); cursor: pointer;
  transition: all 0.15s;
}
#toolbar button:hover { background: var(--accent-light); border-color: var(--accent); }

/* Sidebar */
#sidebar {
  width: 380px; flex-shrink: 0;
  background: var(--surface); border-left: 1px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}
#sidebar .section-title {
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
  color: var(--text-muted); padding: 12px 16px;
  border-bottom: 1px solid var(--border-light);
  background: var(--surface);
  flex-shrink: 0;
}
#event-detail {
  padding: 14px 16px; overflow-y: auto; flex: 1;
}
#event-detail .placeholder { color: var(--text-muted); font-style: italic; }
.detail-row { margin-bottom: 10px; }
.detail-key { color: var(--text-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 2px; }
.detail-val { color: var(--text); word-break: break-all; }
.detail-val.mono { font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace; font-size: 12px; color: #666; }
.detail-val .type-badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
.content-box {
  background: #f9f9f9; border: 1px solid var(--border-light); border-radius: 8px;
  padding: 10px; white-space: pre-wrap; word-break: break-all;
  font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace;
  font-size: 12px; line-height: 1.5;
}

/* Status indicator */
#status {
  position: absolute; bottom: 12px; right: 12px;
  font-size: 11px; color: var(--text-muted);
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 20px; padding: 5px 10px;
  display: flex; align-items: center; gap: 5px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
.live-dot.reconnecting { background: var(--yellow); }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
</style>
</head>
<body>

<div id="topbar">
  <h1>FERN DAG</h1>
  <div class="stats" id="header-info">Loading...</div>
  <div class="spacer"></div>
  <div class="control-group">
    <label>Group</label>
    <select id="group-select" onchange="filterGroup()"></select>
  </div>
  <div class="control-group">
    <input type="text" id="search-input" placeholder="Search type or author..." oninput="filterSearch()" />
  </div>
</div>

<div id="app">
  <div id="network-wrap">
    <div id="network"></div>
    <div id="toolbar">
      <button onclick="networkFit()">Fit</button>
      <button onclick="togglePhysics()">Physics</button>
    </div>
    <div id="legend">
      <h3>Event Types</h3>
      <div id="legend-items"></div>
    </div>
    <div id="status"><span class="live-dot" id="live-dot"></span><span id="status-text">Live</span></div>
  </div>
  <div id="sidebar">
    <div class="section-title">Event Details</div>
    <div id="event-detail"><div class="placeholder">Click an event to see details.</div></div>
  </div>
</div>

<script>
let network = null;
let allEvents = [];
let allEdges = [];
let allEventReceipts = [];
let selectedId = null;
let currentGroup = '';
let searchTerm = '';
let physicsOn = false;

const typeStyles = {
  'genesis':        { color: '#e91e63', shape: 'star',     size: 22, label: 'genesis' },
  'join':           { color: '#4caf50', shape: 'dot',      size: 10, label: 'join' },
  'leave':          { color: '#9e9e9e', shape: 'dot',      size: 10, label: 'leave' },
  'invite':         { color: '#ffc107', shape: 'dot',      size: 12, label: 'invite' },
  'kick':           { color: '#ff5722', shape: 'dot',      size: 12, label: 'kick' },
  'ban':            { color: '#f44336', shape: 'dot',      size: 12, label: 'ban' },
  'unban':          { color: '#8bc34a', shape: 'dot',      size: 12, label: 'unban' },
  'admin_add':      { color: '#9c27b0', shape: 'dot',      size: 12, label: 'admin_add' },
  'admin_remove':   { color: '#673ab7', shape: 'dot',      size: 12, label: 'admin_remove' },
  'relay_update':   { color: '#00bcd4', shape: 'dot',      size: 12, label: 'relay_update' },
  'metadata_update':{ color: '#009688', shape: 'dot',      size: 12, label: 'metadata_update' },
};
const chatStyle    = { color: '#03a9f4', shape: 'dot', size: 10, label: 'chat' };
const defaultStyle = { color: '#ff9800', shape: 'dot', size: 10, label: 'unknown' };

function getStyle(t) {
  if (t.startsWith('chat.')) return chatStyle;
  return typeStyles[t] || defaultStyle;
}

function typeLabel(t) {
  if (t.length > 22) return t.substring(0, 20) + '..';
  return t;
}

function buildNodes(events) {
  return events.map(e => {
    const s = getStyle(e.type);
    return {
      id: e.id,
      label: typeLabel(e.type) + '\n' + e.id.substring(0, 8),
      color: { background: s.color, border: s.color, highlight: { background: s.color, border: '#ffc107' } },
      font: { color: '#2a2a28', size: 11, face: 'monospace', multi: false, strokeWidth: 3, strokeColor: '#ffffffcc' },
      shape: s.shape,
      size: s.size,
      title: e.type + '\n' + e.author.substring(0, 16) + '...\n' + new Date(e.ts * 1000).toLocaleString(),
    };
  });
}

function getFilteredEvents() {
  let events = allEvents;
  if (currentGroup) {
    events = events.filter(e => e.group === currentGroup);
  }
  if (searchTerm) {
    const term = searchTerm.toLowerCase();
    events = events.filter(e =>
      e.type.toLowerCase().includes(term) ||
      e.author.toLowerCase().includes(term) ||
      (e.content && JSON.stringify(e.content).toLowerCase().includes(term))
    );
  }
  return events;
}

function render() {
  const events = getFilteredEvents();
  const eventIds = new Set(events.map(e => e.id));
  const edges = allEdges.filter(ed => eventIds.has(ed.from) && eventIds.has(ed.to));

  const nodes = buildNodes(events);
  const edgeData = edges.map(e => ({
    from: e.from, to: e.to, arrows: 'to',
    color: { color: '#ccc', opacity: 0.5, highlight: '#2196f3' },
    smooth: { type: 'cubicBezier', roundness: 0.4 },
  }));

  const container = document.getElementById('network');
  const data = { nodes: nodes, edges: edgeData };
  const options = {
    layout: { hierarchical: { enabled: true, direction: 'DU', sortMethod: 'directed', levelSeparation: 100, nodeSpacing: 80 } },
    physics: { enabled: physicsOn, stabilization: { iterations: 80 } },
    interaction: { hover: true, tooltipDelay: 150, navigationButtons: false, zoomView: true },
    nodes: { shadow: { enabled: true, size: 4, x: 0, y: 1, color: 'rgba(0,0,0,0.08)' } },
    edges: { width: 1.5 },
  };

  if (network) network.destroy();
  network = new vis.Network(container, data, options);

  network.on('click', function(params) {
    if (params.nodes.length > 0) {
      showDetail(params.nodes[0]);
    } else {
      selectedId = null;
    }
  });

  network.once('afterDrawing', function() {
    network.fit({ animation: { duration: 300, easingFunction: 'easeOutQuad' } });
  });

  if (selectedId && events.find(e => e.id === selectedId)) {
    network.selectNodes([selectedId]);
    showDetail(selectedId);
  }
}

function showDetail(id) {
  selectedId = id;
  const e = allEvents.find(ev => ev.id === id);
  if (!e) return;

  const s = getStyle(e.type);
  const contentStr = JSON.stringify(e.content, null, 2);
  const tsDate = new Date(e.ts * 1000).toISOString();

  let event_receiptsHtml = '';
  const evEventReceipts = allEventReceipts.filter(r => r.event_id === id);
  if (evEventReceipts.length > 0) {
    event_receiptsHtml = '<div class="detail-row"><div class="detail-key">Event receipts</div>';
    for (const r of evEventReceipts) {
      event_receiptsHtml += '<div class="detail-val mono" style="margin-bottom:3px">relay=' +
        r.relay_pubkey.substring(0, 16) + '... ts=' + r.event_receipt.ts + '</div>';
    }
    event_receiptsHtml += '</div>';
  }

  const parentsStr = e.parents.length === 0
    ? '<span style="color:var(--text-muted)">(genesis — no parents)</span>'
    : e.parents.map(p => '<span style="color:#4a6a8a">' + p.substring(0, 12) + '...</span>').join('<br>');

  document.getElementById('event-detail').innerHTML =
    '<div class="detail-row"><div class="detail-key">Type</div><div class="detail-val"><span class="type-badge" style="background:' + s.color + ';color:#fff">' + e.type + '</span></div></div>' +
    '<div class="detail-row"><div class="detail-key">ID</div><div class="detail-val mono">' + e.id + '</div></div>' +
    '<div class="detail-row"><div class="detail-key">Author</div><div class="detail-val mono">' + e.author + '</div></div>' +
    '<div class="detail-row"><div class="detail-key">Group</div><div class="detail-val mono">' + e.group + '</div></div>' +
    '<div class="detail-row"><div class="detail-key">Timestamp</div><div class="detail-val">' + tsDate + ' <span style="color:var(--text-muted)">(' + e.ts + ')</span></div></div>' +
    '<div class="detail-row"><div class="detail-key">Parents (' + e.parents.length + ')</div><div class="detail-val mono">' + parentsStr + '</div></div>' +
    '<div class="detail-row"><div class="detail-key">Signature</div><div class="detail-val mono">' + (e.sig ? e.sig.substring(0, 32) + '...' : '(none)') + '</div></div>' +
    event_receiptsHtml +
    '<div class="detail-row"><div class="detail-key">Content</div><div class="content-box">' + escapeHtml(contentStr) + '</div></div>';

  if (network && network.findNode(id).length > 0) {
    network.selectNodes([id]);
    network.focus(id, { scale: 1.2, animation: { duration: 300, easingFunction: 'easeOutQuad' } });
  }
}

function renderLegend() {
  const container = document.getElementById('legend-items');
  let html = '';
  for (const [t, s] of Object.entries(typeStyles)) {
    if (s.shape === 'star') {
      html += '<div class="legend-row"><span class="legend-star" style="color:' + s.color + '">\u2605</span><span>' + s.label + '</span></div>';
    } else {
      html += '<div class="legend-row"><span class="legend-dot" style="background:' + s.color + '"></span><span>' + s.label + '</span></div>';
    }
  }
  html += '<div class="legend-row"><span class="legend-dot" style="background:' + chatStyle.color + '"></span><span>chat.*</span></div>';
  container.innerHTML = html;
}

function filterGroup() {
  const sel = document.getElementById('group-select');
  currentGroup = sel.value;
  render();
}

function filterSearch() {
  searchTerm = document.getElementById('search-input').value.trim();
  render();
}

function networkFit() {
  if (network) network.fit({ animation: { duration: 400, easingFunction: 'easeOutQuad' } });
}

function togglePhysics() {
  physicsOn = !physicsOn;
  if (network) {
    network.setOptions({ physics: { enabled: physicsOn, stabilization: { iterations: 80 } }, layout: { hierarchical: { enabled: !physicsOn } } });
  }
  render();
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function updateFromData(data) {
  allEvents = data.events || [];
  allEdges = data.edges || [];
  allEventReceipts = data.event_receipts || [];

  const sel = document.getElementById('group-select');
  const groupKeys = Object.keys(data.groups || {});
  const prevValue = sel.value;
  sel.innerHTML = '<option value="">All groups</option>' +
    groupKeys.map(g => '<option value="' + g + '">' + g.substring(0, 16) + '... (' + data.groups[g].count + ')</option>').join('');
  if (prevValue && groupKeys.includes(prevValue)) sel.value = prevValue;
  else if (groupKeys.length === 1) sel.value = groupKeys[0];
  currentGroup = sel.value;

  document.getElementById('header-info').textContent =
    allEvents.length + ' events, ' + allEdges.length + ' edges, ' + groupKeys.length + ' group(s)';

  render();
}

async function fetchInitial() {
  const resp = await fetch('/api/graph');
  const data = await resp.json();
  updateFromData(data);
}

function connectSSE() {
  const es = new EventSource('/api/events/stream');
  const dot = document.getElementById('live-dot');
  es.onmessage = async function() {
    document.getElementById('status-text').textContent = 'Updating...';
    const resp = await fetch('/api/graph');
    const data = await resp.json();
    updateFromData(data);
    document.getElementById('status-text').textContent = 'Live';
    dot.classList.remove('reconnecting');
  };
  es.onerror = function() {
    document.getElementById('status-text').textContent = 'Reconnecting...';
    dot.classList.add('reconnecting');
    es.close();
    setTimeout(connectSSE, 2000);
  };
}

renderLegend();
fetchInitial().then(connectSSE);
</script>
</body>
</html>
"""


class _DAGHandler(BaseHTTPRequestHandler):
    db_path: str = ""
    _watchers: list[_DAGHandler] = []
    _lock = threading.Lock()

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self._send_html(_HTML)
        elif parsed.path == "/api/graph":
            self._send_graph()
        elif parsed.path == "/api/events/stream":
            self._handle_sse()
        elif parsed.path == "/api/health":
            self._send_json({"status": "ok"})
        else:
            self.send_error(404)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_graph(self) -> None:
        try:
            data = _query_db(self.db_path)
            self._send_json(data)
        except Exception as e:
            self._send_json({"error": str(e), "events": [], "edges": [], "groups": {}, "event_receipts": []})

    def _handle_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_count = self._get_event_count()
        with self._lock:
            self._watchers.append(self)

        try:
            while True:
                time.sleep(1)
                current_count = self._get_event_count()
                if current_count != last_count:
                    last_count = current_count
                    self.wfile.write(b"data: update\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self._lock:
                if self in self._watchers:
                    self._watchers.remove(self)

    def _get_event_count(self) -> int:
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute("SELECT COUNT(*) FROM events")
                return int(cursor.fetchone()[0])
            finally:
                conn.close()
        except Exception:
            return 0


class _ThreadingServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True


def launch_viewer(db_path: str, host: str = "127.0.0.1", port: int = 8760) -> None:
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    class _BoundHandler(_DAGHandler):
        pass

    _BoundHandler.db_path = str(db.resolve())

    server = _ThreadingServer((host, port), _BoundHandler)
    print("FERN DAG Viewer")
    print(f"  Database: {db_path}")
    print(f"  URL:      http://{host}:{port}")
    print("  Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        server.shutdown()
