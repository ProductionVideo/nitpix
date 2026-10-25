/* nitpix — web UI (vanilla JS, no build step). */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtBytes = (n) => { if (n == null) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i ? n.toFixed(1) : n) + " " + u[i]; };
const fmtDur = (s) => { if (s == null) return "—"; s = +s; const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${x.toFixed(0).padStart(2, "0")}` : m ? `${m}:${x.toFixed(1).padStart(4, "0")}` : `${x.toFixed(2)}s`; };
const fmtFps = (f) => f == null ? "—" : (+f).toFixed(3).replace(/\.?0+$/, "");
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const RESULT_LABEL = { pass: "Pass", warning: "Warning", fail: "Fail", info: "Info", issue: "Issue", na: "N/A" };
const REVIEW = [["unreviewed", "Unreviewed"], ["in_review", "In review"], ["approved", "Approved"], ["approved_notes", "Approved w/ notes"], ["rejected", "Rejected"]];
const REVIEW_LABEL = Object.fromEntries(REVIEW);

const ICONS = {
  dash: '<path d="M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  review: '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M10 8.5v5l4-2.5z"/><path d="M6 21h12"/>',
  spec: '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"/>',
  report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68 1.65 1.65 0 0 0 10 3.17V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  play: '<path d="M7 4v16l13-8z"/>', pause: '<path d="M7 4h4v16H7zM14 4h4v16h-4z"/>',
  stepf: '<path d="M5 4v16l10-8zM17 4h3v16h-3z"/>', stepb: '<path d="M19 4v16L9 12zM4 4h3v16H4z"/>',
  prev: '<path d="M15 18l-6-6 6-6"/>', next: '<path d="M9 18l6-6-6-6"/>',
  ext: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  pin: '<path d="M12 2v20M5 9l7-7 7 7"/>', flag: '<path d="M4 22V4M4 4h13l-2 4 2 4H4"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>', down: '<path d="M12 5v14M19 12l-7 7-7-7"/>',
};
const icon = (n, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[n] || ""}</svg>`;

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { /* empty */ }
  if (!r.ok) { const e = new Error((data && data.error) || `HTTP ${r.status}`); e.code = data && data.code; e.status = r.status; throw e; }
  return data;
}
function toast(msg, err = false, ms = 3600) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), err ? ms * 1.6 : ms);
}
const fail = (e) => toast(e.message || String(e), true);

// ------------------------------------------------------------------ timecode
function parseRate(s) {
  if (s == null) return null;
  if (typeof s === "number") return s > 0 ? [Math.round(s * 1000), 1000] : null;
  const m = String(s).split("/");
  const n = +m[0], d = m.length > 1 ? +m[1] : 1;
  return n && d ? [n, d] : null;
}
const rateF = (r) => r ? r[0] / r[1] : null;
const isDF = (r) => r && ((r[0] === 30000 && r[1] === 1001) || (r[0] === 60000 && r[1] === 1001));
function tcToFrames(tc, r, drop) {
  const m = /^(\d{1,2})[:;.](\d{2})[:;.](\d{2})([:;.])(\d{2,3})$/.exec(tc.trim());
  if (!m) return null;
  const fps = Math.round(rateF(r));
  if (drop == null) drop = m[4] === ";" && isDF(r);
  let total = ((+m[1] * 60 + +m[2]) * 60 + +m[3]) * fps + +m[5];
  if (drop) { const dn = fps === 30 ? 2 : 4; const tm = +m[1] * 60 + +m[2]; total -= dn * (tm - Math.floor(tm / 10)); }
  return total;
}
function framesToTc(frames, r, drop) {
  if (!r) return "--:--:--:--";
  const fps = Math.round(rateF(r));
  frames = Math.max(0, Math.round(frames));
  if (drop && (fps === 30 || fps === 60)) {
    const dn = fps === 30 ? 2 : 4, per10 = fps * 600 - dn * 9, per1 = fps * 60 - dn;
    const d = Math.floor(frames / per10), m = frames % per10;
    frames += m > dn ? dn * 9 * d + dn * Math.floor((m - dn) / per1) : dn * 9 * d;
  }
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(frames / (fps * 3600)) % 24)}:${p(Math.floor(frames / (fps * 60)) % 60)}:${p(Math.floor(frames / fps) % 60)}${drop ? ";" : ":"}${p(frames % fps)}`;
}
// Converts asset-relative frame indices <-> labels (TC for video/audio, frame number for sequences)
function posCtx(a) {
  if (!a) return null;
  if (a.kind === "sequence" && a.seq) {
    const first = a.seq.first, count = a.seq.last - a.seq.first + 1;
    return { seq: true, first, count, fps: a.fps_float || 25,
      label: (f) => f == null ? "" : String(first + f), sub: (f) => f == null ? "" : `idx ${f}`,
      parse: (s) => { s = s.trim(); if (/^\d+$/.test(s)) return +s - first; return null; } };
  }
  const r = parseRate(a.fps) || [25, 1];
  const drop = !!(a.start_tc && a.start_tc.includes(";") && isDF(r));
  const start = a.start_tc ? (tcToFrames(a.start_tc, r, drop) || 0) : 0;
  const count = a.kind === "image" ? 1 : Math.max(1, a.frames || Math.round((a.duration || 0) * rateF(r)));
  return { seq: false, first: 0, count, fps: rateF(r), rate: r, drop,
    label: (f) => f == null ? "" : framesToTc(start + f, r, drop),
    sub: (f) => f == null ? "" : `fr ${f}`,
    parse: (s) => { s = s.trim(); if (/^\d+$/.test(s)) return +s; if (/^\+\d+$/.test(s)) return null; const v = tcToFrames(s, r, drop); return v == null ? null : v - start; } };
}

// ------------------------------------------------------------------ state
const S = {
  player: (() => { try { return localStorage.getItem("nitpix.player") === "1"; } catch { return false; } })(),
  st: null, view: "dashboard", assets: [], assetsAt: 0, dash: null,
  filters: { q: "", result: "all", kind: "", codec: "", res: "", fps: "", review: "", dmin: "", dmax: "", detections: false },
  sort: { key: "dir", dir: 1 }, cursor: 0, sel: new Set(),
  asset: null, frame: 0, tab: "checks", ff: -1, follow: true,
  iina: null, presets: null, presetSel: null, exports: [], noteDraft: null,
};
const prefs = (() => { try { return JSON.parse(localStorage.getItem("nitpix") || "{}"); } catch { return {}; } })();
const savePrefs = () => { try { localStorage.setItem("nitpix", JSON.stringify(prefs)); } catch { /* ignore */ } };
function applyTheme() { const t = prefs.theme || "system"; if (t === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t); }
applyTheme();

// ------------------------------------------------------------------ boot / routing
async function refreshState() {
  S.st = await api("/api/state");
  renderRail(); renderTop(); renderJob(S.st.job);
}
function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }
async function route() {
  const h = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [view, arg] = h.split("/");
  if (!S.st) await refreshState();
  if (!S.st.project) { S.view = "start"; renderRail(); renderTop(); return renderStart(); }
  S.view = view;
  renderRail(); renderTop();
  try {
    if (view === "dashboard") await viewDashboard();
    else if (view === "media") await viewMedia();
    else if (view === "review" || view === "asset") await viewReview(arg ? +arg : null);
    else if (view === "presets") await viewPresets();
    else if (view === "reports") await viewReports();
    else if (view === "settings") await viewSettings();
    else go("#/dashboard");
  } catch (e) {
    if (e.code === "no_project") { S.st = null; return route(); }
    $("#view").innerHTML = `<div class="page"><div class="banner err">${esc(e.message)}</div></div>`;
  }
}
window.addEventListener("hashchange", route);

function renderRail() {
  const views = [["dashboard", "dash", "Dashboard"], ["media", "list", "Media"], ["review", "review", "Review"], ["presets", "spec", "Presets"], ["reports", "report", "Reports"], ["settings", "gear", "Settings"]];
  const v = S.view === "asset" ? "review" : S.view;
  $("#nav").innerHTML = S.st && S.st.project ? views.map(([k, ic, l], i) => `<button data-view="${k}" class="${v === k ? "on" : ""}" title="${l} (${i + 1})">${icon(ic)}<span>${l}</span><span class="k">${i + 1}</span></button>`).join("") : "";
  const st = S.st || {};
  const ff = st.tools && st.tools.ffmpeg && st.tools.ffmpeg.available && st.tools.ffprobe.available;
  const ii = st.iina || {};
  const live = S.iina && S.iina.connected;
  $("#rail-status").innerHTML = `
    <div class="stat-line" title="${esc(ff ? st.tools.ffmpeg.version : "ffmpeg/ffprobe not found")}"><span class="dot ${ff ? "pass" : "fail"}"></span><span class="l">FFmpeg ${ff ? "ready" : "missing"}</span></div>
    <div class="stat-line" title="${esc(ii.reason || (ii.cli || ""))}"><span class="dot ${live ? "live" : ii.available ? "pass" : "warning"}"></span><span class="l">IINA ${live ? "connected" : ii.available ? "ready" + (ii.version ? " · " + esc(ii.version) : "") : "unavailable"}</span></div>
    <div class="stat-line"><span class="l faint mono" style="font-size:10.5px">? shortcuts</span></div>`;
}
$("#nav").addEventListener("click", (e) => { const b = e.target.closest("button[data-view]"); if (b) go("#/" + b.dataset.view); });

function renderTop() {
  const p = S.st && S.st.project;
  if (!p) { $("#topbar").innerHTML = `<h1>nitpix</h1><span class="sub">Delivery QC with IINA review</span>`; return; }
  const s = p.settings;
  $("#topbar").innerHTML = `
    <div><h1>${esc(s.project_name)}</h1><div class="sub">${esc(s.client || "")}${s.client ? " · " : ""}Preset: ${esc(p.preset.name)}</div></div>
    <div class="spacer"></div>
    <label class="search">${icon("search")}<input id="gsearch" placeholder="Search media…" value="${esc(S.filters.q)}"><kbd>/</kbd></label>
    <button class="btn primary" id="btn-add">${icon("plus")} Add media</button>`;
  $("#gsearch").addEventListener("input", debounce(async (e) => { S.filters.q = e.target.value; if (S.view !== "media") return go("#/media"); await ensureCheckHits(); renderMediaRows(true); }, 120));
  $("#btn-add").onclick = addMediaDialog;
}

// ------------------------------------------------------------------ job bar + polling
let jobTimer = null, lastJobState = null;
function renderJob(j) {
  const el = $("#jobbar");
  if (!j || !S.st || !S.st.project) { el.hidden = true; return; }
  const running = ["running", "paused", "cancelling"].includes(j.state);
  const recentlyDone = j.finished && Date.now() / 1000 - j.finished < 900;
  el.hidden = !(running || (recentlyDone && S.jobDismissed !== j.id));
  el.className = j.state === "paused" ? "paused" : "";
  const pct = Math.round((j.progress || 0) * 100);
  const cur = (j.current || []).map((c) => `${esc(c.name)} <span class="faint">${esc(c.phase)} ${Math.round((c.progress || 0) * 100)}%</span>`).join(" · ");
  const eta = j.eta != null ? `~${fmtDur(j.eta)} left` : "";
  el.innerHTML = `
    <div><b>${{ running: "Scanning", paused: "Paused", cancelling: "Cancelling", done: "Batch complete", cancelled: "Batch cancelled", error: "Batch error" }[j.state] || j.state}</b> <span class="muted">${esc(j.label)}</span></div>
    <div><div class="bar"><i style="width:${running ? pct : 100}%"></i></div>
      <div class="meta"><span><b>${j.done}</b> / ${j.total} assets</span>${j.phase === "discovering" ? `<span>${j.files_seen} files found…</span>` : ""}${j.failed ? `<span style="color:var(--fail)">${j.failed} unreadable</span>` : ""}${j.skipped ? `<span>${j.skipped} unchanged</span>` : ""}<span>${eta}</span><span class="ellipsis" style="max-width:50vw">${cur}</span></div></div>
    <div class="row">${running ? (j.state === "paused" ? `<button class="btn sm" data-job="resume">Resume</button>` : `<button class="btn sm" data-job="pause">Pause</button>`) + `<button class="btn sm danger" data-job="cancel">Cancel</button>` : `<button class="btn sm ghost" data-job="hide" data-id="${esc(j.id)}">Dismiss</button>`}</div>`;
}
$("#jobbar").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-job]"); if (!b) return;
  if (b.dataset.job === "hide") { $("#jobbar").hidden = true; S.jobDismissed = b.dataset.id; return; }
  try { renderJob(await api(`/api/job/${b.dataset.job}`, { method: "POST", body: {} })); pollJob(); } catch (err) { fail(err); }
});
async function pollJob() {
  clearTimeout(jobTimer);
  let running = false;
  try {
    if (!S.st) await refreshState();
    if (!S.st.project) return;
    const j = await api("/api/job");
    running = !!(j && ["running", "paused", "cancelling"].includes(j.state));
    renderJob(j);
    const wasRunning = ["running", "paused", "cancelling"].includes(lastJobState);
    if (running || (j && wasRunning)) {
      if (Date.now() - S.assetsAt > 2500 || !running) {
        await loadAssets();
        if (S.view === "media") renderMediaRows();
        if (S.view === "dashboard") viewDashboard(true);
        if (!running && (S.view === "review" || S.view === "asset") && S.asset) refreshAsset();
      }
    }
    if (j && wasRunning && !running) toast(j.state === "done" ? `Batch complete: ${j.done} assets checked` : `Batch ${j.state}`);
    lastJobState = j ? j.state : null;
  } catch { /* keep polling */ }
  finally { jobTimer = setTimeout(pollJob, running ? 800 : 3000); }
}
async function loadAssets() { S.assets = await api("/api/assets"); S.assetsAt = Date.now(); }

// ------------------------------------------------------------------ start screen
function renderStart() {
  const rec = (S.st.recent || []);
  const tools = S.st.tools;
  const ffOk = tools.ffmpeg.available && tools.ffprobe.available;
  $("#view").innerHTML = `
  <div class="start">
    <div class="hero"><h1>Delivery QC, frame by frame.</h1><p class="muted" style="margin:0;max-width:640px">Scan large video, audio, still and image-sequence deliveries, check them against a delivery spec, then confirm every automated finding in IINA or the built-in frame viewer.</p>
    ${ffOk ? "" : `<div class="banner err" style="margin-top:14px"><b>FFmpeg not found.</b>&nbsp;Install it with <code>brew install ffmpeg</code>; metadata and content checks need ffprobe and ffmpeg.</div>`}</div>
    <div class="card"><div class="hd"><h2>New project</h2></div><div class="bd" style="display:flex;flex-direction:column;gap:10px">
      <div class="field"><label>Project name</label><input type="text" id="np-name" placeholder="e.g. Client X — Spring campaign masters"></div>
      <div class="field"><label>Save in folder</label><div class="row"><input type="text" id="np-dir" style="flex:1" value="~/nitpix Projects"><button class="btn sm" id="np-pick">Choose…</button></div>
      <span class="hint">Creates <span class="mono">&lt;name&gt;.nitpix</span> holding the review database, cache and exports. Media is never copied or modified.</span></div>
      <div><button class="btn primary" id="np-go">Create project</button></div></div></div>
    <div class="card"><div class="hd"><h2>Open project</h2><span class="spacer"></span><button class="btn sm" id="op-demo">Load demo delivery</button></div>
      <div class="bd flush recent">${rec.length ? rec.map((r) => `<div data-open="${esc(r.path)}"><b>${esc(r.name)}</b><br><span class="mono faint" style="font-size:11px">${esc(r.path)}</span></div>`).join("") : `<div class="muted" style="cursor:default">No recent projects</div>`}</div>
      <div class="bd" style="border-top:1px solid var(--line)"><div class="row"><input type="text" id="op-path" placeholder="/path/to/project.nitpix" style="flex:1"><button class="btn" id="op-go">Open</button></div></div></div>
  </div>`;
  $("#np-pick").onclick = async () => { const r = await api("/api/pick", { body: { what: "project" } }); if (r.paths && r.paths[0]) $("#np-dir").value = r.paths[0]; else if (r.unsupported) toast("Type a folder path (native picker is macOS only)"); };
  $("#np-go").onclick = async () => {
    const name = $("#np-name").value.trim() || "Untitled delivery";
    const slug = name.replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "_") || "Untitled";
    try { await api("/api/project/open", { body: { path: $("#np-dir").value.replace(/\/$/, "") + "/" + slug, name } }); S.st = null; go("#/dashboard"); addMediaDialog(); } catch (e) { fail(e); }
  };
  const open = async (p) => { try { await api("/api/project/open", { body: { path: p } }); S.st = null; go("#/dashboard"); pollJob(); } catch (e) { fail(e); } };
  $$("[data-open]").forEach((d) => d.onclick = () => open(d.dataset.open));
  $("#op-go").onclick = () => open($("#op-path").value.trim());
  $("#op-demo").onclick = async () => {
    $("#op-demo").disabled = true; $("#op-demo").textContent = "Generating demo media…";
    try { await api("/api/project/demo", { body: {} }); S.st = null; lastJobState = "running"; go("#/dashboard"); pollJob(); } catch (e) { fail(e); $("#op-demo").disabled = false; }
  };
}

// ------------------------------------------------------------------ add media
function addMediaDialog() {
  const roots = (S.st.project && S.st.project.roots) || [];
  modal(`
    <div class="hd"><b>Add media</b><span class="spacer" style="flex:1"></span><button class="btn ghost sm" data-close>${icon("x")}</button></div>
    <div class="bd">
      <div class="row"><button class="btn" id="am-folder">${icon("folder")} Choose folders…</button><button class="btn" id="am-files">${icon("file")} Choose files…</button><button class="btn ghost" id="am-browse">Browse here</button></div>
      <div class="field"><label>Paths to scan (one per line)</label><textarea id="am-paths" rows="5" class="mono" placeholder="/Volumes/Delivery/Client_X/2026-10-24">${esc(roots.length ? "" : "")}</textarea>
        <span class="hint">Folders are scanned recursively. Image sequences are grouped automatically by filename pattern. Files are read only — nothing is written to the delivery.</span></div>
      <div id="am-browser" hidden></div>
      ${roots.length ? `<div class="field"><label>Previously scanned</label><div class="row">${roots.map((r) => `<button class="btn sm" data-root="${esc(r)}">${esc(r.split("/").slice(-2).join("/"))}</button>`).join("")}</div></div>` : ""}
      <label class="check"><input type="checkbox" id="am-force"> Re-check files that haven't changed since the last scan</label>
    </div>
    <div class="ft"><button class="btn" data-close>Cancel</button><button class="btn primary" id="am-go">Start scan</button></div>`);
  const ta = $("#am-paths");
  const addPaths = (ps) => { const cur = ta.value.split("\n").map((x) => x.trim()).filter(Boolean); ta.value = [...new Set([...cur, ...ps])].join("\n"); };
  const pick = async (what) => { try { const r = await api("/api/pick", { body: { what } }); if (r.paths) addPaths(r.paths); else if (r.unsupported) { toast("Native picker is macOS only — use Browse here"); browse("~"); } } catch (e) { fail(e); } };
  $("#am-folder").onclick = () => pick("folder");
  $("#am-files").onclick = () => pick("files");
  $("#am-browse").onclick = () => browse(ta.value.split("\n")[0] || "~");
  $$("[data-root]").forEach((b) => b.onclick = () => addPaths([b.dataset.root]));
  async function browse(path) {
    let r; try { r = await api("/api/ls?path=" + encodeURIComponent(path)); } catch (e) { return fail(e); }
    const box = $("#am-browser"); box.hidden = false;
    box.innerHTML = `<div class="row" style="margin-bottom:6px"><span class="mono ellipsis" style="flex:1">${esc(r.path)}</span><button class="btn sm" data-addhere>Add this folder</button></div>
      <div class="browser"><div data-nav="${esc(r.parent)}">${icon("prev")} ..</div>${r.entries.map((e) => e.dir ? `<div data-nav="${esc(e.path)}">${icon("folder")} ${esc(e.name)}</div>` : `<div class="file" data-addfile="${esc(e.path)}">${icon("file")} ${esc(e.name)}</div>`).join("")}</div>`;
    $$("[data-nav]", box).forEach((d) => d.onclick = () => browse(d.dataset.nav));
    $$("[data-addfile]", box).forEach((d) => d.ondblclick = () => addPaths([d.dataset.addfile]));
    $("[data-addhere]", box).onclick = () => addPaths([r.path]);
  }
  $("#am-go").onclick = async () => {
    const roots = ta.value.split("\n").map((x) => x.trim()).filter(Boolean);
    if (!roots.length) return toast("Add at least one path", true);
    try { await api("/api/scan", { body: { roots, force: $("#am-force").checked } }); closeModal(); lastJobState = "running"; pollJob(); if (S.view !== "media" && S.view !== "dashboard") go("#/dashboard"); } catch (e) { fail(e); }
  };
  setTimeout(() => ta.focus(), 30);
}
function modal(html) { const m = $("#modal"); m.innerHTML = `<div class="dlg">${html}</div>`; m.hidden = false; $$("[data-close]", m).forEach((b) => b.onclick = closeModal); }
function closeModal() { $("#modal").hidden = true; $("#modal").innerHTML = ""; }
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") closeModal(); });

// ------------------------------------------------------------------ dashboard
async function viewDashboard(soft = false) {
  const d = await api("/api/dashboard");
  S.dash = d;
  if (!S.assets.length || !soft) await loadAssets();
  const r = d.by_result || {}, rv = d.by_review || {}, ch = d.checks || {};
  const total = d.assets.n || 0;
  const scanned = (r.pass || 0) + (r.warning || 0) + (r.fail || 0);
  const pending = (d.by_state.pending || 0) + (d.by_state.probing || 0) + (d.by_state.analysing || 0);
  const kinds = Object.entries(d.by_kind || {}).map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(" · ");
  const signoff = S.st.project.signoff;
  if (!total) {
    $("#view").innerHTML = `<div class="page"><div class="card"><div class="bd empty"><h2 style="margin-bottom:6px">No media yet</h2><p>Add a delivery folder to start the batch.</p><button class="btn primary" id="d-add">${icon("plus")} Add media</button></div></div></div>`;
    $("#d-add").onclick = addMediaDialog; return;
  }
  const catRows = (d.by_category || []).filter((c) => c.category !== "spec").map((c) => {
    const t = (c.fail || 0) + (c.warning || 0) + (c.pass || 0) || 1;
    return `<div class="b" data-cat="${esc(c.category)}"><span style="text-transform:capitalize">${esc(c.category)}</span>
      <span class="track"><i style="width:${(c.fail / t) * 100}%;background:var(--fail)"></i><i style="width:${(c.warning / t) * 100}%;background:var(--warn)"></i><i style="width:${(c.pass / t) * 100}%;background:var(--pass);opacity:.6"></i></span>
      <span class="mono" style="font-size:11px;text-align:right">${c.fail ? `<span style="color:var(--fail)">${c.fail}</span>` : ""}${c.fail && c.warning ? "·" : ""}${c.warning ? `<span style="color:var(--warn)">${c.warning}</span>` : ""}${!c.fail && !c.warning ? "✓" : ""}</span></div>`;
  }).join("");
  const top = (d.top_issues || []).map((t) => `<div class="b" data-check="${esc(t.check_key)}"><span>${esc(checkName(t.check_key))}</span>
      <span class="track"><i style="width:${Math.min(100, (t.assets / total) * 100)}%;background:${t.fails ? "var(--fail)" : "var(--warn)"}"></i></span><span class="mono" style="font-size:11px;text-align:right">${t.assets}</span></div>`).join("") || `<div class="empty">No warnings or failures.</div>`;
  const att = (d.needs_attention || []).map((a) => `<tr data-asset="${a.id}"><td><span class="dot ${a.scan_state === "error" ? "fail" : a.qc_result || "pending"}"></span></td>
      <td class="name ellipsis"><b>${esc(a.name)}</b></td><td>${issueCounts(a)}</td><td>${a.open_detections ? `<span class="chip warning">${a.open_detections} to confirm</span>` : ""}</td><td><span class="chip ${a.review_status}">${REVIEW_LABEL[a.review_status]}</span></td></tr>`).join("");
  $("#view").innerHTML = `<div class="page">
    <div class="kpis">
      <div class="kpi" data-goto="all"><div class="lbl">Assets</div><div class="val">${total}</div><div class="sub">${fmtBytes(d.assets.bytes)} · ${esc(kinds)}</div></div>
      <div class="kpi pass" data-goto="pass"><div class="lbl"><span class="dot pass"></span>Passed</div><div class="val">${r.pass || 0}</div><div class="sub">${scanned ? Math.round(((r.pass || 0) / scanned) * 100) : 0}% of checked assets</div></div>
      <div class="kpi warning" data-goto="warning"><div class="lbl"><span class="dot warning"></span>Warnings</div><div class="val">${r.warning || 0}</div><div class="sub">${ch.warning || 0} warning checks</div></div>
      <div class="kpi fail" data-goto="fail"><div class="lbl"><span class="dot fail"></span>Failed</div><div class="val">${r.fail || 0}</div><div class="sub">${ch.fail || 0} failed checks</div></div>
      <div class="kpi" data-goto="detections"><div class="lbl">To confirm</div><div class="val">${d.open_detections}</div><div class="sub">automated detections awaiting a decision</div></div>
      <div class="kpi" data-goto="review"><div class="lbl">Signed off</div><div class="val">${(rv.approved || 0) + (rv.approved_notes || 0) + (rv.rejected || 0)}<span class="muted" style="font-size:16px"> / ${total}</span></div><div class="sub">${rv.approved || 0} approved · ${rv.approved_notes || 0} w/ notes · ${rv.rejected || 0} rejected</div></div>
    </div>
    ${pending ? `<div class="banner">${pending} asset(s) still to check. Results update live while the batch runs.</div>` : ""}
    <div class="grid" style="grid-template-columns: minmax(0,1.4fr) minmax(0,1fr)">
      <div class="card"><div class="hd"><h2>Needs attention</h2><span class="spacer"></span><button class="btn sm ghost" data-goto="fail">All failures →</button></div>
        <div class="bd flush">${att ? `<table class="tbl"><tbody>${att}</tbody></table>` : `<div class="empty">Nothing needs attention.</div>`}</div></div>
      <div class="grid">
        <div class="card"><div class="hd"><h2>Results by category</h2></div><div class="bd flush bars">${catRows}</div></div>
        <div class="card"><div class="hd"><h2>Most common problems</h2><span class="muted" style="font-size:11px">assets affected</span></div><div class="bd flush bars">${top}</div></div>
      </div>
    </div>
    <div class="grid" style="grid-template-columns: minmax(0,1.4fr) minmax(0,1fr)">
      <div class="card"><div class="hd"><h2>Batch log</h2><span class="spacer"></span>${d.job ? `<span class="muted">${esc(d.job.label)}</span>` : ""}</div><div class="log">${d.job ? esc(d.job.log.join("\n")) : "No batch run yet in this session."}</div></div>
      <div class="card"><div class="hd"><h2>Batch sign-off</h2></div><div class="bd">${signoff ? `<div class="row"><span class="chip ${signoff.status === "rejected" ? "fail" : signoff.status === "approved" ? "pass" : "warning"}">${esc(signoffLabel(signoff.status))}</span><span>${esc(signoff.by)}</span><span class="muted">${fmtTime(signoff.at)}</span></div>${signoff.note ? `<p class="muted" style="margin:8px 0 0">${esc(signoff.note)}</p>` : ""}` : `<p class="muted" style="margin:0 0 10px">Not signed off.</p>`}<div style="margin-top:10px"><button class="btn sm" onclick="location.hash='#/reports'">Sign off & export →</button></div></div></div>
    </div></div>`;
  $$("[data-asset]").forEach((tr) => tr.onclick = () => go("#/asset/" + tr.dataset.asset));
  $$("[data-goto]").forEach((k) => k.onclick = () => {
    const g = k.dataset.goto;
    Object.assign(S.filters, { result: "all", detections: false, review: "" });
    if (["pass", "warning", "fail"].includes(g)) S.filters.result = g;
    if (g === "detections") S.filters.detections = true;
    if (g === "review") S.filters.review = "unreviewed";
    go("#/media");
  });
  $$("[data-check]").forEach((k) => k.onclick = () => { Object.assign(S.filters, { result: "all", q: "check:" + k.dataset.check }); go("#/media"); });
}
const signoffLabel = (s) => ({ approved: "Approved for delivery", approved_notes: "Approved with notes", rejected: "Rejected — redeliver" }[s] || s);
const CHECK_NAMES = {
  "integrity.zero_byte": "Zero-byte file", "integrity.readable": "Unreadable file", "integrity.decode": "Decode errors", "integrity.stream_durations": "A/V duration mismatch",
  "format.container": "Container", "format.codec": "Codec / profile", "format.resolution": "Resolution", "format.fps": "Frame rate", "format.scan": "Scan type", "format.duration": "Duration",
  "format.aspect": "Pixel aspect ratio", "format.orientation": "Orientation", "format.timecode": "Timecode", "colour.tags": "Colour metadata", "colour.bit_depth": "Bit depth",
  "colour.chroma": "Chroma subsampling", "colour.alpha": "Alpha channel", "colour.hdr": "HDR", "audio.presence": "Audio presence", "audio.codec": "Audio codec", "audio.sample_rate": "Sample rate",
  "audio.channels": "Audio channels", "audio.bit_depth": "Audio bit depth", "audio.loudness": "Loudness", "audio.true_peak": "True peak", "content.black": "Black frames", "content.freeze": "Frozen frames",
  "content.flash": "Flash frames", "content.silence": "Silence", "content.clipping": "Clipping", "content.channel_dropout": "Channel drop-out", "seq.naming": "Sequence naming", "seq.range": "Frame range",
  "seq.missing": "Missing frames", "seq.duplicate_numbers": "Duplicate frame numbers", "seq.padding": "Inconsistent padding", "seq.repeated": "Repeated frames", "seq.dimensions": "Inconsistent dimensions",
  "seq.unreadable": "Unreadable frames", "seq.size_outlier": "Suspicious frame size", "seq.format": "Sequence format",
};
const checkName = (k) => CHECK_NAMES[k] || k;
function issueCounts(a) {
  if (a.scan_state === "pending" || a.scan_state === "probing" || a.scan_state === "analysing") return `<span class="faint">${a.scan_state}…</span>`;
  if (!a.fails && !a.warnings) return `<span class="counts faint">—</span>`;
  return `<span class="counts">${a.fails ? `<span class="f">${a.fails} fail</span>` : ""}${a.warnings ? `<span class="w">${a.warnings} warn</span>` : ""}</span>`;
}

// ------------------------------------------------------------------ media list
function filteredAssets() {
  const f = S.filters;
  let q = f.q.trim().toLowerCase(), checkQ = null;
  const m = /check:(\S+)/.exec(q); if (m) { checkQ = m[1]; q = q.replace(m[0], "").trim(); }
  let out = S.assets.filter((a) => {
    if (q && !(`${a.name} ${a.dir} ${a.vcodec || ""} ${a.container || ""} ${a.width || ""}x${a.height || ""}`.toLowerCase().includes(q))) return false;
    if (f.result !== "all") {
      if (f.result === "error" ? a.scan_state !== "error" : f.result === "pending" ? !["pending", "probing", "analysing"].includes(a.scan_state) : a.qc_result !== f.result) return false;
    }
    if (f.kind && a.kind !== f.kind) return false;
    if (f.codec && (a.vcodec || a.acodec || a.ext) !== f.codec) return false;
    if (f.res && `${a.width}×${a.height}` !== f.res) return false;
    if (f.fps && fmtFps(a.fps_float) !== f.fps) return false;
    if (f.review && a.review_status !== f.review) return false;
    if (f.detections && !a.open_detections) return false;
    if (f.dmin !== "" && (a.duration == null || a.duration < +f.dmin)) return false;
    if (f.dmax !== "" && (a.duration == null || a.duration > +f.dmax)) return false;
    return true;
  });
  if (checkQ) {
    const ids = new Set((S.checkHits && S.checkHits[checkQ]) || []);
    if (S.checkHits && S.checkHits[checkQ]) out = out.filter((a) => ids.has(a.id));
  }
  const k = S.sort.key, dir = S.sort.dir;
  const rank = { fail: 0, warning: 1, pass: 2 };
  const val = (a) => k === "qc_result" ? (rank[a.qc_result] ?? 3) : k === "issues" ? -(a.fails * 1000 + a.warnings) : k === "res" ? (a.width || 0) * (a.height || 0) : k === "dir" ? `${a.dir}/${a.name}` : a[k] ?? "";
  out.sort((x, y) => { const a = val(x), b = val(y); return (a < b ? -1 : a > b ? 1 : 0) * dir; });
  return out;
}
async function ensureCheckHits() {
  const m = /check:(\S+)/.exec(S.filters.q.toLowerCase());
  if (!m) return;
  S.checkHits = S.checkHits || {};
  S.checkHits[m[1]] = await api("/api/assets?check=" + encodeURIComponent(m[1]));
}
async function viewMedia() {
  await loadAssets();
  if (/check:/.test(S.filters.q)) await ensureCheckHits();
  const uniq = (fn) => [...new Set(S.assets.map(fn).filter((x) => x != null && x !== "" && x !== "null×null" && x !== "—"))].sort();
  const counts = { all: S.assets.length, fail: 0, warning: 0, pass: 0, error: 0, pending: 0 };
  S.assets.forEach((a) => { if (a.qc_result) counts[a.qc_result]++; if (a.scan_state === "error") counts.error++; if (["pending", "probing", "analysing"].includes(a.scan_state)) counts.pending++; });
  const f = S.filters;
  const opt = (vals, cur, all) => `<option value="">${all}</option>` + vals.map((v) => `<option ${String(v) === String(cur) ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#view").innerHTML = `<div class="page full" style="gap:10px">
    <div class="filters">
      <div class="seg" id="f-result">${[["all", "All"], ["fail", "Fail"], ["warning", "Warning"], ["pass", "Pass"], ["error", "Errors"], ["pending", "Pending"]].map(([k, l]) => `<button data-r="${k}" class="${f.result === k ? "on" : ""}">${l} <span class="n">${counts[k] || 0}</span></button>`).join("")}</div>
      <select id="f-kind">${opt(uniq((a) => a.kind), f.kind, "All types")}</select>
      <select id="f-codec">${opt(uniq((a) => a.vcodec || a.acodec || a.ext), f.codec, "All formats")}</select>
      <select id="f-res">${opt(uniq((a) => a.width ? `${a.width}×${a.height}` : null), f.res, "All resolutions")}</select>
      <select id="f-fps">${opt(uniq((a) => a.fps_float ? fmtFps(a.fps_float) : null), f.fps, "All frame rates")}</select>
      <select id="f-review"><option value="">Any review status</option>${REVIEW.map(([k, l]) => `<option value="${k}" ${f.review === k ? "selected" : ""}>${l}</option>`).join("")}</select>
      <span class="row" style="gap:4px"><input type="number" id="f-dmin" placeholder="min s" style="width:72px" value="${esc(f.dmin)}">–<input type="number" id="f-dmax" placeholder="max s" style="width:72px" value="${esc(f.dmax)}"></span>
      <label class="check"><input type="checkbox" id="f-det" ${f.detections ? "checked" : ""}> Has unconfirmed detections</label>
      <button class="btn sm ghost" id="f-clear">Clear</button>
    </div>
    <div class="row" id="bulk"></div>
    <div class="card" style="flex:1;min-height:0;display:flex;flex-direction:column"><div class="listwrap" id="listwrap" tabindex="0"><table class="tbl" id="mtable"><thead><tr>
      <th style="width:28px"><input type="checkbox" id="selall"></th>
      ${[["qc_result", "", "width:26px"], ["dir", "Name"], ["kind", "Type"], ["vcodec", "Format"], ["res", "Resolution"], ["fps_float", "FPS", "", "r"], ["duration", "Duration", "", "r"], ["audio_channels", "Audio"], ["issues", "Issues"], ["open_detections", "To confirm"], ["review_status", "Review"]].map(([k, l, st, c]) => `<th data-sort="${k}" class="${c || ""}" style="${st || ""}">${l}${S.sort.key === k ? (S.sort.dir > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}
    </tr></thead><tbody id="mrows"></tbody></table></div></div></div>`;
  $$("#f-result button").forEach((b) => b.onclick = () => { f.result = b.dataset.r; viewMedia(); });
  const bind = (id, key, ev = "change") => $(id).addEventListener(ev, (e) => { f[key] = e.target.type === "checkbox" ? e.target.checked : e.target.value; S.cursor = 0; renderMediaRows(true); });
  bind("#f-kind", "kind"); bind("#f-codec", "codec"); bind("#f-res", "res"); bind("#f-fps", "fps"); bind("#f-review", "review"); bind("#f-det", "detections");
  bind("#f-dmin", "dmin", "input"); bind("#f-dmax", "dmax", "input");
  $("#f-clear").onclick = () => { Object.assign(f, { q: "", result: "all", kind: "", codec: "", res: "", fps: "", review: "", dmin: "", dmax: "", detections: false }); if ($("#gsearch")) $("#gsearch").value = ""; viewMedia(); };
  $$("#mtable th[data-sort]").forEach((th) => th.onclick = () => { const k = th.dataset.sort; S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : 1 }; viewMedia(); });
  $("#selall").onchange = (e) => { const list = filteredAssets(); if (e.target.checked) list.forEach((a) => S.sel.add(a.id)); else S.sel.clear(); renderMediaRows(); };
  const wrap = $("#listwrap");
  wrap.addEventListener("scroll", () => requestAnimationFrame(() => renderMediaRows()));
  $("#mrows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return;
    const id = +tr.dataset.id;
    if (e.target.matches("input[type=checkbox]")) { e.target.checked ? S.sel.add(id) : S.sel.delete(id); renderBulk(); return; }
    if (e.target.closest("a")) return;
    S.cursor = +tr.dataset.i;
    $$("#mrows tr.cur").forEach((r) => r.classList.remove("cur")); tr.classList.add("cur");
  });
  $("#mrows").addEventListener("dblclick", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) go("#/asset/" + tr.dataset.id); });
  renderMediaRows(true);
}
let mList = [];
function renderMediaRows(recompute = false) {
  const body = $("#mrows"); if (!body) return;
  if (recompute || !mList.length || mList.length !== filteredAssets().length) mList = filteredAssets();
  else mList = filteredAssets();
  S.cursor = Math.max(0, Math.min(S.cursor, mList.length - 1));
  const wrap = $("#listwrap");
  const RH = 34, H = wrap.clientHeight || 600;
  const start = Math.max(0, Math.floor(wrap.scrollTop / RH) - 10), end = Math.min(mList.length, start + Math.ceil(H / RH) + 20);
  const rows = [];
  rows.push(`<tr style="height:${start * RH}px"></tr>`);
  for (let i = start; i < end; i++) {
    const a = mList[i];
    const st = a.scan_state === "error" ? "fail" : ["pending", "probing", "analysing"].includes(a.scan_state) ? "pending" : a.qc_result || "pending";
    const fmt = a.kind === "sequence" ? `${(a.ext || "").toUpperCase()} seq · ${a.frames || 0} fr` : [a.container, a.vcodec].filter(Boolean).join(" · ") || (a.acodec || a.ext || "—");
    rows.push(`<tr data-id="${a.id}" data-i="${i}" class="${i === S.cursor ? "cur" : ""} ${S.sel.has(a.id) ? "selrow" : ""}">
      <td><input type="checkbox" ${S.sel.has(a.id) ? "checked" : ""}></td>
      <td><span class="dot ${st}" title="${esc(a.scan_error || st)}"></span></td>
      <td class="name ellipsis"><a href="#/asset/${a.id}" style="text-decoration:none"><b>${esc(a.name)}</b></a> <span class="p">${esc(shortDir(a.dir))}</span></td>
      <td>${esc(a.kind)}</td><td class="ellipsis" style="max-width:180px">${esc(fmt)}</td>
      <td class="mono">${a.width ? `${a.width}×${a.height}` : "—"}</td><td class="r mono">${a.fps_float ? fmtFps(a.fps_float) : "—"}</td>
      <td class="r mono">${a.kind === "image" ? "—" : fmtDur(a.duration)}</td>
      <td class="mono">${a.audio_channels ? `${a.audio_channels}ch ${a.sample_rate ? a.sample_rate / 1000 + "k" : ""}` : '<span class="faint">—</span>'}</td>
      <td>${a.scan_state === "error" ? `<span class="chip fail" title="${esc(a.scan_error)}">${esc((a.scan_error || "error").slice(0, 34))}</span>` : issueCounts(a)}</td>
      <td>${a.open_detections ? `<span class="chip warning">${a.open_detections}</span>` : ""}</td>
      <td><span class="chip ${a.review_status === "approved" ? "pass" : a.review_status === "approved_notes" ? "pass" : a.review_status === "rejected" ? "fail" : "unreviewed"}">${REVIEW_LABEL[a.review_status] || ""}</span></td></tr>`);
  }
  rows.push(`<tr style="height:${(mList.length - end) * RH}px"></tr>`);
  body.innerHTML = mList.length ? rows.join("") : `<tr><td colspan="12" class="empty">No media matches these filters.</td></tr>`;
  renderBulk();
}
const shortDir = (d) => { const p = (d || "").split("/").filter(Boolean); return p.slice(-2).join("/"); };
function renderBulk() {
  const b = $("#bulk"); if (!b) return;
  const n = S.sel.size;
  b.innerHTML = `<span class="muted">${mList.length} shown${n ? ` · <b style="color:var(--ink)">${n} selected</b>` : ""}</span><span class="spacer" style="flex:1"></span>
    <span class="faint" style="font-size:12px">↑↓ move · Enter open · x select · e open in IINA</span>
    ${n ? `<button class="btn sm" data-b="approved">Approve</button><button class="btn sm" data-b="rejected">Reject</button><button class="btn sm" data-b="reeval">Re-evaluate</button><button class="btn sm" data-b="rescan">Re-scan</button><button class="btn sm" data-b="export">Export selected</button><button class="btn sm danger" data-b="remove">Remove from project</button><button class="btn sm ghost" data-b="clear">Clear</button>` : ""}`;
  $$("[data-b]", b).forEach((x) => x.onclick = async () => {
    const ids = [...S.sel], k = x.dataset.b;
    try {
      if (k === "clear") { S.sel.clear(); return renderMediaRows(); }
      if (k === "approved" || k === "rejected") await api("/api/assets/review", { body: { ids, review_status: k } });
      if (k === "reeval" || k === "rescan") { await api("/api/recheck", { body: { ids, reanalyse: k === "rescan" } }); lastJobState = "running"; pollJob(); }
      if (k === "export") { S.exportIds = ids; return go("#/reports"); }
      if (k === "remove") { if (!confirm(`Remove ${ids.length} asset(s) and their review data from this project? Media files are not touched.`)) return; await api("/api/assets/remove", { body: { ids } }); S.sel.clear(); }
      await loadAssets(); renderMediaRows(true);
    } catch (e) { fail(e); }
  });
}

// ------------------------------------------------------------------ review
let iinaTimer = null, frameReq = 0;
async function loadAsset(id, keepFrame = false) {
  const a = await api("/api/asset/" + id);
  const prev = S.asset;
  S.asset = a;
  S.ctx = posCtx(a);
  if (!keepFrame || !prev || prev.id !== a.id) { S.frame = Math.min(a.last_frame || 0, Math.max(0, S.ctx.count - 1)); S.ff = -1; }
  return a;
}
async function viewReview(id) {
  if (!S.assets.length) await loadAssets();
  if (!id) id = (S.asset && S.asset.id) || (filteredAssets()[0] || {}).id;
  if (!id) { $("#view").innerHTML = `<div class="page"><div class="card"><div class="bd empty">No asset selected. Pick one from Media.</div></div></div>`; return; }
  await loadAsset(id, S.asset && S.asset.id === id);
  mList = filteredAssets();
  renderReview();
  startIinaPoll();
}
function renderReview() {
  const a = S.asset, c = S.ctx;
  const idx = mList.findIndex((x) => x.id === a.id);
  const resChip = `<span class="chip ${a.scan_state === "error" ? "fail" : a.qc_result || "pending"}">${a.scan_state === "error" ? "Error" : RESULT_LABEL[a.qc_result] || "Pending"}</span>`;
  const iinaEnv = S.st.iina || {};
  $("#view").innerHTML = `<div class="review">
    <div class="left">
      <div class="asset-head">
        <button class="btn ghost sm" id="rv-prev" title="Previous asset (,)" ${idx <= 0 ? "disabled" : ""}>${icon("prev")}</button>
        <button class="btn ghost sm" id="rv-next" title="Next asset (.)" ${idx < 0 || idx >= mList.length - 1 ? "disabled" : ""}>${icon("next")}</button>
        <div class="t"><b class="ellipsis">${esc(a.name)} ${resChip}</b><span class="p ellipsis" title="${esc(a.path)}">${esc(a.path)}</span></div>
        ${S.st.platform === "Darwin" ? `<button class="btn ghost sm" id="rv-reveal" title="Reveal in Finder">${icon("folder")}</button>` : ""}
        <div class="seg" id="rv-status">${REVIEW.map(([k, l]) => `<button data-s="${k}" class="${a.review_status === k ? "on" : ""}">${l}</button>`).join("")}</div>
      </div>
      ${a.exists === false ? `<div class="banner err" style="margin:10px 16px">This file is no longer at its recorded path. Review data is kept; re-scan once the media is reconnected.</div>` : ""}
      <div class="viewer" id="viewer">${viewerInner()}</div>
      <div class="iina" id="iina-bar"></div>
      <div class="transport">
        <div><div class="tc" id="tc">${esc(c.label(S.frame))}</div><div class="tc-sub" id="tcsub">${esc(c.sub(S.frame))} / ${c.count} ${c.seq ? "frames" : "frames"}</div></div>
        <div class="btn-group">
          <button class="btn" data-t="home" title="Start (Home)">|◀</button>
          <button class="btn" data-t="back" title="Back 1 frame (←)">${icon("stepb")}</button>
          <button class="btn" data-t="play" title="Play / pause (Space): the browser player when it is on, otherwise IINA" id="btn-play">${icon("play")}</button>
          <button class="btn" data-t="fwd" title="Forward 1 frame (→)">${icon("stepf")}</button>
          <button class="btn" data-t="end" title="End (End)">▶|</button>
        </div>
        <input type="text" class="tcin" id="tcin" placeholder="${c.seq ? "frame #" : "00:00:00:00"}" title="Go to ${c.seq ? "frame number" : "timecode or frame"} (g)">
        <div class="btn-group">
          <button class="btn sm" data-t="in" title="Set in (i)">In ${a.in_frame != null ? `<span class="mono">${esc(c.label(a.in_frame))}</span>` : ""}</button>
          <button class="btn sm" data-t="out" title="Set out (o)">Out ${a.out_frame != null ? `<span class="mono">${esc(c.label(a.out_frame))}</span>` : ""}</button>
          <button class="btn sm" data-t="clrio" title="Clear in/out">×</button>
        </div>
        <button class="btn sm" data-t="bookmark" title="Bookmark this frame (b)">${icon("flag")} Mark</button>
        <button class="btn sm" data-t="note" title="Note at this frame (n)">+ Note</button>
        <span style="flex:1"></span>
        <div class="btn-group"><button class="btn sm" data-t="prevf" title="Previous finding ([)">${icon("prev")}</button><button class="btn sm" data-t="nextf" title="Next finding (])">Finding ${icon("next")}</button></div>
      </div>
      <div class="timeline" id="timeline"></div>
      ${a.kind === "sequence" ? `<div style="padding:12px 16px" id="seqpanel"></div>` : ""}
    </div>
    <div class="right">
      <div class="tabs" id="rtabs"></div>
      <div class="pane" id="rpane"></div>
    </div></div>`;
  $("#rv-prev").onclick = () => idx > 0 && go("#/asset/" + mList[idx - 1].id);
  $("#rv-next").onclick = () => idx < mList.length - 1 && go("#/asset/" + mList[idx + 1].id);
  if ($("#rv-reveal")) $("#rv-reveal").onclick = () => api("/api/reveal", { body: { path: a.kind === "sequence" ? a.dir : a.path } });
  $$("#rv-status button").forEach((b) => b.onclick = () => setReview(b.dataset.s));
  $$(".transport [data-t]").forEach((b) => b.onclick = () => transport(b.dataset.t));
  $("#tcin").addEventListener("keydown", (e) => { if (e.key === "Enter") { const f = c.parse(e.target.value); if (f == null || f < 0 || f >= c.count) return toast("Not a valid position for this asset", true); seek(f); e.target.blur(); } if (e.key === "Escape") e.target.blur(); });
  renderIinaBar(); renderTimeline(); renderTabs(); renderPane();
  if (a.kind === "sequence") renderSeqPanel();
  $$(".vmode button").forEach((b) => b.onclick = () => setPlayerMode(b.dataset.vm === "player"));
  updateViewer(true);
  if (S.player && (a.kind === "video" || a.kind === "sequence")) startPlayer();
}
function viewerInner() {
  const a = S.asset;
  if (a.kind === "audio") return `<div class="msg">Audio asset — no picture.<br>Use IINA to listen; the timeline shows per-channel level.</div>`;
  if (a.kind === "other" || a.scan_state === "error" && a.kind !== "sequence") return `<div class="msg">${esc(a.scan_error || "No picture available.")}</div>`;
  const playable = a.kind === "video" || a.kind === "sequence";
  const mode = playable ? `<div class="vmode" title="Frame viewer: exact ffmpeg frames. Player: plays a browser proxy made with ffmpeg (p)"><button data-vm="frame" class="${S.player ? "" : "on"}">Frames</button><button data-vm="player" class="${S.player ? "on" : ""}">Player</button></div>` : "";
  return `<img id="vimg" alt=""><video id="vvid" playsinline preload="auto" hidden></video><div class="ovl"><span id="vsrc">Frame viewer</span><span id="vpos"></span></div>${mode}<div class="msg" id="vmsg" hidden></div>`;
}

// ---- browser player: plays an ffmpeg proxy (H.264) of the asset ----------------------
// The proxy keeps the asset's exact rate with frame n at n / rate, so the player seeks to
// frame centres and reads the presented frame back with requestVideoFrameCallback.
let pv = null;  // {aid, rate, ready, poll}
const playerOn = () => !!(S.player && pv && pv.ready && pv.aid === S.asset.id && $("#vvid"));
function setPlayerMode(on) {
  S.player = !!on;
  try { localStorage.setItem("nitpix.player", S.player ? "1" : "0"); } catch { /* private window */ }
  $$(".vmode button").forEach((b) => b.classList.toggle("on", (b.dataset.vm === "player") === S.player));
  if (S.player) startPlayer(); else stopPlayer();
}
function stopPlayer() {
  const v = $("#vvid");
  if (v) { v.pause(); v.hidden = true; }
  if (pv && pv.poll) clearTimeout(pv.poll);
  if ($("#vimg")) $("#vimg").hidden = false;
  updateViewer(true);
}
async function startPlayer() {
  const a = S.asset;
  if (!(a.kind === "video" || a.kind === "sequence")) return;
  if (pv && pv.poll) clearTimeout(pv.poll);
  pv = { aid: a.id, ready: false };
  const show = (txt) => { const m = $("#vmsg"); if (m) { m.hidden = !txt; m.textContent = txt || ""; } };
  const check = async (first) => {
    if (!S.player || !pv || pv.aid !== S.asset.id) return;
    let st;
    try { st = await api(`/api/proxy/${a.id}`, first ? { body: {} } : undefined); } catch (e) { show(""); return fail(e); }
    const [n, d] = st.rate.split("/").map(Number); pv.rate = n / (d || 1);
    if (st.state === "ready") { show(""); return attachPlayer(st.url); }
    if (st.state === "error") { show(`Could not make a browser proxy: ${st.error}`); return; }
    $("#vsrc").textContent = `Making browser proxy… ${Math.round((st.progress || 0) * 100)}%`; $("#vsrc").className = "";
    pv.poll = setTimeout(() => check(false), 800);
  };
  check(true);
}
function attachPlayer(url) {
  const v = $("#vvid"), img = $("#vimg");
  if (!v || !pv) return;
  pv.ready = true;
  if (!v.src.endsWith(url)) v.src = url;
  v.hidden = false; if (img) img.hidden = true;
  $("#vsrc").textContent = "Player (browser proxy)"; $("#vsrc").className = "";
  const onFrame = (_, meta) => {
    if (!playerOn() || $("#vvid") !== v) return;
    const f = Math.round(meta.mediaTime * pv.rate);
    const a = S.asset;
    if (!v.paused && a.in_frame != null && a.out_frame != null && f > a.out_frame) playerSeek(a.in_frame);
    else if (f !== S.frame) setFrame(f, "player");
    v.requestVideoFrameCallback(onFrame);
  };
  if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(onFrame);
  else v.ontimeupdate = () => playerOn() && setFrame(Math.floor(v.currentTime * pv.rate), "player");
  v.onplay = v.onpause = () => { const b = $("#btn-play"); if (b) b.innerHTML = icon(v.paused ? "play" : "pause"); };
  const go0 = () => playerSeek(S.frame);
  if (v.readyState >= 1) go0(); else v.onloadedmetadata = go0;
}
function playerSeek(f) {
  const v = $("#vvid");
  if (v && pv && pv.rate) v.currentTime = (f + 0.5) / pv.rate;
}
const updateViewer = debounce((force) => {
  const img = $("#vimg"); if (!img) return;
  if (playerOn()) { $("#vpos").textContent = S.ctx.label(S.frame); return; }
  const live = S.iina && S.iina.connected && S.iina.session && S.iina.session.asset_id === S.asset.id;
  if (live && S.iina.paused === false && !force) { $("#vsrc").textContent = "Playing in IINA"; $("#vsrc").className = "live"; return; }
  const my = ++frameReq;
  const url = `/api/frame?asset=${S.asset.id}&frame=${S.frame}&w=1280`;
  const tmp = new Image();
  tmp.onload = () => { if (my !== frameReq) return; img.src = url; img.hidden = false; $("#vmsg").hidden = true; $("#vpos").textContent = S.ctx.label(S.frame); $("#vsrc").textContent = live ? "IINA frame (synced)" : "Frame viewer"; $("#vsrc").className = live ? "live" : ""; };
  tmp.onerror = async () => { if (my !== frameReq) return; let msg = "Cannot render this frame."; try { const r = await fetch(url); const j = await r.json(); msg = j.error || msg; } catch { /* ignore */ } img.hidden = true; const m = $("#vmsg"); m.hidden = false; m.textContent = msg; $("#vpos").textContent = S.ctx.label(S.frame); };
  tmp.src = url;
}, 90);

function setFrame(f, from = null) {
  const c = S.ctx;
  f = Math.max(0, Math.min(c.count - 1, Math.round(f)));
  const changed = f !== S.frame;
  S.frame = f;
  if ($("#tc")) { $("#tc").textContent = c.label(f); $("#tcsub").textContent = `${c.sub(f)} / ${c.count} frames`; }
  positionPlayhead();
  if (changed) { updateViewer(); saveLastFrame(); if (from !== "player" && playerOn() && $("#vvid").paused) playerSeek(f); }
  if (S.asset.kind === "sequence") highlightSeqCell();
  return changed;
}
const saveLastFrame = debounce(() => S.asset && api("/api/asset/" + S.asset.id, { body: { last_frame: S.frame } }).catch(() => {}), 1500);
const iinaLive = () => S.iina && S.iina.connected && S.iina.session && S.asset && S.iina.session.asset_id === S.asset.id;
async function iinaCmd(body) { try { S.iina = await api("/api/iina/cmd", { body }); if (S.iina.frame != null && S.follow) setFrame(S.iina.frame, true); renderIinaBar(); } catch (e) { fail(e); } }
function seek(f) {
  setFrame(f);
  if (iinaLive()) iinaCmd({ cmd: "seek", frame: S.frame });
}
function step(n) {
  if (iinaLive() && Math.abs(n) === 1) { setFrame(S.frame + n); iinaCmd({ cmd: "step", n }); }
  else seek(S.frame + n);
}
async function transport(t) {
  const a = S.asset, c = S.ctx;
  if (t === "home") seek(0); else if (t === "end") seek(c.count - 1);
  else if (t === "back") step(-1); else if (t === "fwd") step(1);
  else if (t === "play") { if (playerOn()) { const v = $("#vvid"); v.paused ? v.play() : v.pause(); } else if (iinaLive()) iinaCmd({ cmd: "pause" }); else if (S.player) toast("The browser proxy is still being made"); else openInIina(); }
  else if (t === "in" || t === "out") {
    const k = t === "in" ? "in_frame" : "out_frame";
    a[k] = S.frame;
    if (a.in_frame != null && a.out_frame != null && a.out_frame < a.in_frame) [a.in_frame, a.out_frame] = [a.out_frame, a.in_frame];
    await api("/api/asset/" + a.id, { body: { in_frame: a.in_frame, out_frame: a.out_frame } });
    if (iinaLive()) iinaCmd({ cmd: "loop", in: a.in_frame, out: a.out_frame });
    renderReview();
  } else if (t === "clrio") { a.in_frame = a.out_frame = null; await api("/api/asset/" + a.id, { body: { in_frame: null, out_frame: null } }); if (iinaLive()) iinaCmd({ cmd: "loop", in: null, out: null }); renderReview(); }
  else if (t === "bookmark") { await api("/api/notes", { body: { asset_id: a.id, kind: "bookmark", frame: S.frame, severity: "info", text: "Bookmark" } }); toast(`Bookmarked ${c.label(S.frame)}`); if (iinaLive()) iinaCmd({ cmd: "osd", text: `Bookmark ${c.label(S.frame)}` }); await refreshAsset(); }
  else if (t === "note") { S.tab = "notes"; S.noteDraft = { frame: S.frame }; renderTabs(); renderPane(); setTimeout(() => $("#nf-text") && $("#nf-text").focus(), 20); }
  else if (t === "prevf" || t === "nextf") jumpFinding(t === "nextf" ? 1 : -1);
}
function markers() {
  // findings + notes with positions, sorted
  const a = S.asset;
  const fs = a.findings.filter((f) => f.frame_in != null && f.check_key !== "seq.range").map((f) => ({ t: "f", id: f.id, frame: f.frame_in, out: f.frame_out, res: f.reviewer_status || f.auto_result }));
  const ns = a.notes.filter((n) => n.frame != null).map((n) => ({ t: "n", id: n.id, frame: n.frame, out: n.frame_out, kind: n.kind }));
  return [...fs, ...ns].sort((x, y) => x.frame - y.frame);
}
function jumpFinding(dir) {
  const ms = markers().filter((m) => m.t === "f" && m.res !== "pass" && m.res !== "info");
  if (!ms.length) return toast("No positioned findings on this asset");
  let m = dir > 0 ? ms.find((x) => x.frame > S.frame) : [...ms].reverse().find((x) => x.frame < S.frame);
  if (!m) m = dir > 0 ? ms[0] : ms[ms.length - 1];
  seek(m.frame);
  focusFinding(m.id);
}
function focusFinding(fid) {
  S.tab = "checks"; renderTabs();
  const list = visibleFindings();
  S.ff = list.findIndex((f) => f.id === fid);
  renderPane();
  const el = $(`.fd[data-fid="${fid}"]`); if (el) el.scrollIntoView({ block: "nearest" });
}
async function setReview(status) {
  try {
    await api("/api/asset/" + S.asset.id, { body: { review_status: status } });
    await refreshAsset();
    const unresolved = S.asset.findings.filter((f) => f.kind === "detected" && ["warning", "fail"].includes(f.auto_result) && !f.reviewer_status).length;
    if ((status === "approved" || status === "approved_notes") && unresolved) toast(`Signed off with ${unresolved} detection(s) still unconfirmed`, true);
    else toast(`${S.asset.name}: ${REVIEW_LABEL[status]}`);
  } catch (e) { fail(e); }
}
async function refreshAsset() {
  await loadAsset(S.asset.id, true);
  const i = S.assets.findIndex((x) => x.id === S.asset.id);
  if (i >= 0) Object.assign(S.assets[i], { qc_result: S.asset.qc_result, review_status: S.asset.review_status });
  renderReview();
}

// ---- IINA bar + polling
function renderIinaBar() {
  const el = $("#iina-bar"); if (!el) return;
  const env = (S.iina && S.iina.env) || S.st.iina || {};
  const live = iinaLive();
  const other = S.iina && S.iina.connected && S.iina.session && S.asset && S.iina.session.asset_id !== S.asset.id;
  const pic = S.asset.kind !== "other";
  if (!env.available) {
    el.innerHTML = `<span class="dot warning"></span><span><b>IINA unavailable.</b> ${esc(env.reason || "")}</span>`;
    return;
  }
  el.innerHTML = `<span class="dot ${live ? "live" : "pending"}"></span>
    <span>${live ? `<b>Connected to IINA</b> · ${S.iina.paused ? "paused" : "playing"} · position <span class="mono">${esc(S.ctx.label(S.iina.frame))}</span>` : other ? `IINA is showing <b>${esc(S.iina.session.name)}</b>` : "Not open in IINA"}</span>
    ${pic ? `<button class="btn sm ${live ? "" : "primary"}" id="ii-open" title="Open in IINA at current position (e)">${icon("ext")} ${live ? "Re-open" : "Open in IINA"}</button>` : ""}
    ${live ? `<label class="check" title="Follow IINA's playback position (f)"><input type="checkbox" id="ii-follow" ${S.follow ? "checked" : ""}> Follow IINA</label><button class="btn sm" id="ii-grab" title="Take IINA's current frame">Use IINA position</button>` : ""}
    ${S.iina && S.iina.error ? `<span class="err">${esc(S.iina.error)}</span>` : ""}`;
  if ($("#ii-open")) $("#ii-open").onclick = openInIina;
  if ($("#ii-follow")) $("#ii-follow").onchange = (e) => { S.follow = e.target.checked; };
  if ($("#ii-grab")) $("#ii-grab").onclick = () => S.iina.frame != null && setFrame(S.iina.frame, true);
  const pb = $("#btn-play"); if (pb && !playerOn()) pb.innerHTML = live && S.iina.paused === false ? icon("pause") : icon("play");
}
async function openInIina() {
  if (S.asset.kind === "audio" || S.asset.kind === "video" || S.asset.kind === "sequence" || S.asset.kind === "image") {
    $("#iina-bar").innerHTML = `<span class="dot pending"></span><span>Opening in IINA…</span>`;
    try { S.iina = await api("/api/iina/open", { body: { asset_id: S.asset.id, frame: S.frame, loop: true } }); if (S.iina.error) toast(S.iina.error, true, 7000); }
    catch (e) { S.iina = Object.assign(S.iina || {}, { error: e.message }); fail(e); }
    renderIinaBar(); renderRail(); startIinaPoll();
  }
}
function startIinaPoll() {
  clearTimeout(iinaTimer);
  const tick = async () => {
    if (S.view !== "review" && S.view !== "asset") return;
    if (S.iina && S.iina.session) {
      try {
        const prevConn = S.iina.connected, prevPaused = S.iina.paused;
        S.iina = await api("/api/iina/state");
        if (iinaLive() && S.follow && S.iina.frame != null) setFrame(S.iina.frame, true);
        if (prevConn !== S.iina.connected || prevPaused !== S.iina.paused) { renderIinaBar(); renderRail(); if (S.iina.paused) updateViewer(true); }
        else { const p = $("#iina-bar .mono"); if (p && iinaLive()) p.textContent = S.ctx.label(S.iina.frame); }
      } catch { /* ignore */ }
    }
    iinaTimer = setTimeout(tick, S.iina && S.iina.connected && S.iina.paused === false ? 200 : 600);
  };
  tick();
}

// ---- timeline
function renderTimeline() {
  const a = S.asset, c = S.ctx, el = $("#timeline"); if (!el) return;
  if (a.kind === "image" || a.kind === "other") { el.innerHTML = `<div class="muted" style="font-size:12px">Single image — no timeline.</div>`; return; }
  const N = Math.max(1, c.count);
  const pct = (f) => (f / N) * 100;
  const w = (f0, f1) => Math.max(0.25, ((Math.max(f1 ?? f0, f0) - f0 + 1) / N) * 100);
  const fmk = a.findings.filter((f) => f.frame_in != null && f.check_key !== "seq.range" && f.auto_result !== "pass")
    .map((f) => `<i class="mk ${f.reviewer_status || f.auto_result}" style="left:${pct(f.frame_in)}%;width:${w(f.frame_in, f.frame_out)}%" title="${esc(f.title)} · ${esc(c.label(f.frame_in))}" data-fid="${f.id}" data-f="${f.frame_in}"></i>`).join("");
  const nmk = a.notes.filter((n) => n.frame != null).map((n) => `<i class="mk ${n.kind === "bookmark" ? "bookmark" : "note"}" style="left:${pct(n.frame)}%;width:${n.kind === "bookmark" ? "3px" : w(n.frame, n.frame_out) + "%"}" title="${esc(n.text)} · ${esc(c.label(n.frame))}" data-f="${n.frame}"></i>`).join("");
  const meta = a.metadata || {}, an = meta.analysis || {};
  let curves = "";
  if (an.yavg && an.yavg.length) curves += lane("Luma", sparkline(an.yavg, 0, 255, "var(--ink-2)"), true);
  if (an.audio_rms && an.audio_rms.channels) {
    Object.entries(an.audio_rms.channels).slice(0, 8).forEach(([ch, vals]) => { curves += lane("A" + ch, sparkline(vals.map((v) => v == null ? -90 : v), -70, 0, "var(--focus)", true), false); });
  }
  if (a.kind === "sequence") curves += lane("Frames", seqBins(200, true), false);
  el.innerHTML = `<div class="tl" id="tl">
      ${lane("Checks", fmk, false)}${lane("Notes", nmk, false)}${curves}
      ${a.in_frame != null || a.out_frame != null ? `<div class="inout" style="left:${pct(a.in_frame ?? 0)}%;width:${w(a.in_frame ?? 0, a.out_frame ?? N - 1)}%"></div>` : ""}
      <div class="ph" id="ph"></div>
    </div>
    <div class="tl ruler"><span>${esc(c.label(0))}</span><span>${esc(c.label(Math.floor(N / 4)))}</span><span>${esc(c.label(Math.floor(N / 2)))}</span><span>${esc(c.label(Math.floor((3 * N) / 4)))}</span><span>${esc(c.label(N - 1))}</span></div>`;
  const tl = $("#tl");
  const fAt = (e) => { const r = tl.getBoundingClientRect(); return Math.max(0, Math.min(N - 1, Math.floor(((e.clientX - r.left) / r.width) * N))); };
  let drag = false;
  tl.addEventListener("mousedown", (e) => { drag = true; const mk = e.target.closest("[data-fid]"); if (mk) { seek(+mk.dataset.f); focusFinding(+mk.dataset.fid); return; } const nm = e.target.closest("[data-f]"); seek(nm ? +nm.dataset.f : fAt(e)); });
  window.onmousemove = (e) => { if (drag) setFrame(fAt(e)); const hv = $("#tlhover"); if (!tl.isConnected) return; const r = tl.getBoundingClientRect(); if (e.clientY < r.top - 20 || e.clientY > r.bottom || e.clientX < r.left || e.clientX > r.right) { hv && hv.remove(); return; } const h = hv || Object.assign(document.createElement("div"), { id: "tlhover", className: "hover" }); h.style.left = `${((e.clientX - r.left) / r.width) * 100}%`; h.textContent = c.label(fAt(e)); if (!hv) tl.appendChild(h); };
  window.onmouseup = () => { if (drag) { drag = false; if (iinaLive()) iinaCmd({ cmd: "seek", frame: S.frame }); } };
  positionPlayhead();
}
const lane = (label, inner, tall) => `<div class="lane ${tall ? "tall" : ""}"><span class="lbl">${label}</span>${inner}</div>`;
function sparkline(vals, lo, hi, color, fill = false) {
  if (!vals.length) return "";
  const n = vals.length, pts = vals.map((v, i) => `${(i / Math.max(1, n - 1)) * 1000},${100 - ((Math.max(lo, Math.min(hi, v ?? lo)) - lo) / (hi - lo)) * 100}`).join(" ");
  return `<svg class="curve" viewBox="0 0 1000 100" preserveAspectRatio="none"><polyline points="${fill ? "0,100 " + pts + " 1000,100" : pts}" fill="${fill ? color : "none"}" fill-opacity="${fill ? 0.25 : 0}" stroke="${color}" stroke-width="1.2" vector-effect="non-scaling-stroke"/></svg>`;
}
function positionPlayhead() { const ph = $("#ph"); if (ph) ph.style.left = `${(S.frame / Math.max(1, S.ctx.count)) * 100}%`; }

// ---- sequence
const SEQ_RANK = { missing: 6, bad: 5, dup: 4, odd: 3, rep: 2, small: 1, ok: 0 };
function seqStatusArray() {
  const s = S.asset.seq; if (!s) return [];
  const N = s.last - s.first + 1, arr = new Array(N).fill("missing");
  (s.frames || []).forEach((r) => { const i = r[0] - s.first; if (i >= 0 && i < N && (arr[i] === "missing" || SEQ_RANK[r[3]] > SEQ_RANK[arr[i]])) arr[i] = r[3] || "ok"; });
  return arr;
}
function seqBins(maxBins, asLane) {
  const arr = seqStatusArray(), N = arr.length, B = Math.min(maxBins, N), out = [];
  for (let b = 0; b < B; b++) {
    const i0 = Math.floor((b * N) / B), i1 = Math.max(i0 + 1, Math.floor(((b + 1) * N) / B));
    let worst = "ok"; for (let i = i0; i < i1; i++) if (SEQ_RANK[arr[i]] > SEQ_RANK[worst]) worst = arr[i];
    out.push([i0, i1 - 1, worst]);
  }
  if (asLane) return out.filter((x) => x[2] !== "ok").map(([i0, i1, st]) => `<i class="mk ${st === "missing" || st === "bad" || st === "dup" || st === "odd" ? "fail" : "warning"}" style="left:${(i0 / N) * 100}%;width:${Math.max(0.3, ((i1 - i0 + 1) / N) * 100)}%" data-f="${i0}" title="${st}"></i>`).join("");
  return out;
}
function renderSeqPanel() {
  const s = S.asset.seq, el = $("#seqpanel"); if (!s || !el) return;
  const N = s.last - s.first + 1, bins = seqBins(Math.min(N, 600), false);
  const cols = Math.min(bins.length, 150), rows = Math.ceil(bins.length / cols);
  const cnt = {}; seqStatusArray().forEach((x) => cnt[x] = (cnt[x] || 0) + 1);
  const L = [["ok", "OK", "color-mix(in srgb, var(--pass) 30%, var(--panel))"], ["missing", "Missing", "var(--fail)"], ["bad", "Unreadable / zero-byte", "var(--fail)"], ["odd", "Wrong size", "#D9480F"], ["dup", "Duplicate number", "#7048E8"], ["rep", "Repeated content", "var(--warn)"], ["small", "Small file", "var(--warn)"]];
  el.innerHTML = `<div class="row" style="margin-bottom:8px"><h2>Sequence</h2><span class="mono muted">${esc(s.pattern)}</span><span class="spacer"></span>
      <span class="mono">${s.first}–${s.last}</span><span class="muted">${s.count} of ${N} present · ${fmtBytes(s.total_size)}</span></div>
    <div class="seqstrip" id="seqstrip" style="grid-template-columns:repeat(${cols},1fr);--cell:${rows > 2 ? 12 : 26}px">${bins.map(([i0, i1, st]) => `<i class="${st}" data-i="${i0}" title="${i0 === i1 ? s.first + i0 : `${s.first + i0}–${s.first + i1}`} · ${st}"></i>`).join("")}</div>
    <div class="legend" style="margin-top:8px">${L.filter(([k]) => cnt[k]).map(([k, l, col]) => `<span><i style="background:${k === "missing" ? "repeating-linear-gradient(135deg,var(--fail) 0 3px,var(--fail-bg) 3px 6px)" : col}"></i>${l} <b class="mono">${cnt[k]}</b></span>`).join("")}</div>
    ${s.missing.length ? `<div class="muted" style="margin-top:8px;font-size:12px">Gaps: <span class="mono">${s.missing.slice(0, 30).map(([x, y]) => x === y ? x : `${x}–${y}`).join(", ")}${s.missing.length > 30 ? "…" : ""}</span></div>` : ""}
    ${bins.length < N ? `<div class="faint" style="font-size:11px;margin-top:4px">Each cell covers ${Math.ceil(N / bins.length)} frames and shows the worst status in that range.</div>` : ""}`;
  $("#seqstrip").onclick = (e) => { const c = e.target.closest("[data-i]"); if (c) seek(+c.dataset.i); };
  highlightSeqCell();
}
function highlightSeqCell() {
  const strip = $("#seqstrip"); if (!strip) return;
  $$("i.cur", strip).forEach((x) => x.classList.remove("cur"));
  const cells = $$("i", strip); let best = null;
  for (const c of cells) { if (+c.dataset.i <= S.frame) best = c; else break; }
  if (best) best.classList.add("cur");
}

// ---- right pane
function visibleFindings() {
  const fs = S.asset.findings;
  const det = fs.filter((f) => f.kind === "detected" && f.auto_result !== "pass");
  const prob = fs.filter((f) => f.kind === "measured" && ["fail", "warning"].includes(f.auto_result));
  return [...det, ...prob];
}
function renderTabs() {
  const a = S.asset;
  const open = a.findings.filter((f) => f.kind === "detected" && ["warning", "fail"].includes(f.auto_result) && !f.reviewer_status).length;
  const tabs = [["checks", "Checks", open ? `<span class="chip warning">${open}</span>` : ""], ["notes", "Notes", `<span class="n">${a.notes.length}</span>`], ["meta", "Metadata", ""]];
  if (a.kind === "sequence") tabs.push(["frames", "Frames", ""]);
  $("#rtabs").innerHTML = tabs.map(([k, l, n]) => `<button data-tab="${k}" class="${S.tab === k ? "on" : ""}">${l} ${n}</button>`).join("");
  $$("#rtabs button").forEach((b) => b.onclick = () => { S.tab = b.dataset.tab; renderTabs(); renderPane(); });
}
function renderPane() {
  const el = $("#rpane"); if (!el) return;
  if (S.tab === "checks") el.innerHTML = paneChecks();
  else if (S.tab === "notes") el.innerHTML = paneNotes();
  else if (S.tab === "meta") el.innerHTML = paneMeta();
  else if (S.tab === "frames") el.innerHTML = paneFrames();
  bindPane();
}
function findingRow(f, i) {
  const c = S.ctx, eff = f.reviewer_status || f.auto_result;
  const pos = f.frame_in != null && f.check_key !== "seq.range" ? `<span class="pos" data-seek="${f.frame_in}">${esc(c.label(f.frame_in))}${f.frame_out != null && f.frame_out !== f.frame_in ? " – " + esc(c.label(f.frame_out)) : ""}</span>` : "";
  const dec = [["pass", "Pass", "1"], ["issue", "Issue", "2"], ["warning", "Warning", "3"], ["na", "N/A", "4"]].map(([k, l, n]) => `<button class="${k} ${f.reviewer_status === k ? "on" : ""}" data-dec="${k}" title="${l} (${n})">${l}</button>`).join("");
  return `<div class="fd ${i === S.ff ? "focus" : ""}" data-fid="${f.id}" data-i="${i}">
    <div class="ti"><span class="chip ${eff}">${RESULT_LABEL[eff] || eff}</span>${esc(f.title)}${f.kind === "detected" ? `<span class="tag detected" title="Automated detection — may be wrong">detected · ${esc(f.confidence)}</span>` : `<span class="tag">measured</span>`}</div>
    <div>${pos}</div>
    ${f.measured || f.expected ? `<div class="mv">${f.measured ? `<span><span class="x">measured</span> ${esc(f.measured)}</span>` : ""}${f.expected ? `<span><span class="x">expected</span> ${esc(f.expected)}</span>` : ""}</div>` : ""}
    ${f.detail ? `<div class="dt">${esc(f.detail)}</div>` : ""}
    ${f.why ? `<div class="why">${esc(f.why)}</div>` : ""}
    <div class="act"><div class="decide">${dec}</div>${f.reviewer_status ? `<button class="btn sm ghost" data-dec="">Clear</button><span class="faint" style="font-size:11px">${esc(f.reviewed_by || "")} ${fmtTime(f.reviewed_at)}</span>` : f.auto_result !== "pass" && f.auto_result !== "info" ? `<span class="faint" style="font-size:11px">awaiting decision</span>` : ""}</div>
    <div class="act" style="grid-column:1/-1"><textarea class="${f.reviewer_note ? "has" : ""}" placeholder="Reviewer note for this finding…" data-fnote>${esc(f.reviewer_note || "")}</textarea></div>
  </div>`;
}
function paneChecks() {
  const a = S.asset, fs = a.findings;
  const vis = visibleFindings();
  const det = vis.filter((f) => f.kind === "detected"), prob = vis.filter((f) => f.kind === "measured");
  const rest = fs.filter((f) => !vis.includes(f));
  const restRow = (f) => { const eff = f.reviewer_status || f.auto_result; return `<div class="fd" data-fid="${f.id}" style="grid-template-columns:auto 1fr auto"><span class="chip ${eff}">${RESULT_LABEL[eff] || eff}</span><div><b style="font-weight:500">${esc(f.title)}</b>${f.measured ? ` <span class="mono muted" style="font-size:11.5px">${esc(f.measured)}</span>` : ""}${f.expected ? ` <span class="faint mono" style="font-size:11px">(exp ${esc(f.expected)})</span>` : ""}<div class="faint" style="font-size:11.5px">${esc(f.why)}</div></div><span class="tag">${f.kind}</span></div>`; };
  let i = 0;
  return `${a.scan_error ? `<div class="banner err" style="margin:10px 14px">${esc(a.scan_error)}</div>` : ""}
    ${det.length ? `<div class="group-h"><h3>Detections — confirm or dismiss</h3><span style="flex:1"></span>${det.some((f) => !f.reviewer_status) ? `<span class="faint" style="font-size:11px">all open:</span><div class="decide"><button class="pass" data-bulk="pass" title="Mark every open detection as Pass">Pass</button><button class="issue" data-bulk="issue" title="Mark every open detection as Issue">Issue</button><button class="na" data-bulk="na">N/A</button></div>` : ""}</div>${det.map((f) => findingRow(f, i++)).join("")}` : ""}
    ${prob.length ? `<div class="group-h"><h3>Measured problems</h3></div>${prob.map((f) => findingRow(f, i++)).join("")}` : ""}
    ${!det.length && !prob.length ? `<div class="empty">No problems found by automated checks.<br><span class="faint">Review visually, then set the review status above.</span></div>` : ""}
    <details class="passed" ${!det.length && !prob.length ? "open" : ""}><summary>${rest.length} passed / informational checks ▾</summary>${rest.map(restRow).join("")}</details>`;
}
function paneNotes() {
  const a = S.asset, c = S.ctx, d = S.noteDraft;
  const form = `<div class="note-form">
    <div class="row"><b>${d && d.id ? "Edit note" : "New note"}</b><span class="mono muted">at ${esc(c.label(d ? d.frame : S.frame))}</span>
      ${a.in_frame != null && a.out_frame != null ? `<label class="check" style="font-size:12px"><input type="checkbox" id="nf-range" ${d && d.frame_out != null ? "checked" : ""}> use in/out range</label>` : ""}<span class="spacer"></span>
      <select id="nf-sev"><option value="info">Info</option><option value="warning" ${d && d.severity === "warning" ? "selected" : ""}>Warning</option><option value="issue" ${d && d.severity === "issue" ? "selected" : ""}>Issue</option></select></div>
    <textarea id="nf-text" placeholder="What did you see? (⌘↩ to save)">${esc(d && d.text || "")}</textarea>
    <div class="row"><span class="faint" style="font-size:11.5px">Visual observations are reported separately from measured results.</span><span class="spacer"></span>${d ? `<button class="btn sm ghost" id="nf-cancel">Cancel</button>` : ""}<button class="btn sm primary" id="nf-save">Save note</button></div></div>`;
  const list = a.notes.map((n) => `<div class="note" data-nid="${n.id}">
      <span class="chip ${n.kind === "bookmark" ? "outline" : n.severity === "issue" ? "fail" : n.severity === "warning" ? "warning" : "info"}">${n.kind === "bookmark" ? "Bookmark" : n.severity}</span>
      <div><span class="pos mono" data-seek="${n.frame ?? ""}" style="color:var(--focus);cursor:pointer">${esc(c.label(n.frame))}${n.frame_out != null && n.frame_out !== n.frame ? " – " + esc(c.label(n.frame_out)) : ""}</span><div class="txt">${esc(n.text)}</div></div>
      <div class="row" style="gap:2px"><button class="btn sm ghost" data-nedit="${n.id}">Edit</button><button class="btn sm ghost danger" data-ndel="${n.id}">${icon("x")}</button></div>
      <div class="by">${esc(n.author || "")} · ${fmtTime(n.created)}</div></div>`).join("");
  const iinfo = a.kind === "video" || a.kind === "audio" ? `<div class="row iinfo-sync"><span class="faint" style="font-size:11.5px">Markers made in IINA with the IINfo plugin are added here on each scan.</span><span class="spacer"></span><button class="btn sm" id="nf-iinfo" title="Add new IINfo markers, then write all markers and notes to ${esc(a.name)}.iinfo.json so IINfo shows them in IINA">Sync with IINfo</button></div>` : "";
  return form + iinfo + (list || `<div class="empty">No notes or bookmarks yet.<br><span class="faint">Press <kbd>n</kbd> for a note, <kbd>b</kbd> for a bookmark.</span></div>`);
}
function paneMeta() {
  const a = S.asset, m = a.metadata || {};
  const rows = [];
  const sec = (t) => rows.push(`<div class="sec">${esc(t)}</div><div class="sec"></div>`);
  const kv = (k, v) => { if (v != null && v !== "") rows.push(`<div>${esc(k)}</div><div>${esc(v)}</div>`); };
  sec("File");
  kv("Path", a.path); kv("Size", fmtBytes(a.size) + (a.size ? ` (${a.size.toLocaleString()} bytes)` : "")); kv("Modified", fmtTime(a.mtime)); kv("Container", m.container_long || a.container);
  kv("Duration", a.duration != null ? `${a.duration.toFixed(3)} s` : null); kv("Bit rate", m.bit_rate ? `${(m.bit_rate / 1e6).toFixed(1)} Mb/s` : null); kv("Start timecode", a.start_tc);
  (m.video_streams || []).forEach((v) => {
    sec(`Video stream #${v.index}`);
    kv("Codec", `${v.codec_long || v.codec}${v.profile ? " · " + v.profile : ""}${v.codec_tag ? ` (${v.codec_tag})` : ""}`); kv("Resolution", `${v.width}×${v.height}${v.rotation ? ` · rotated ${v.rotation}°` : ""}`);
    kv("Frame rate", `${v.fps} (${fmtFps(v.fps_float)})${v.vfr ? " · VFR suspected" : ""}`); kv("Frames", v.frames); kv("Scan", v.field_order); kv("PAR / DAR", `${v.sar} / ${v.dar}`); kv("Orientation", v.orientation);
    kv("Pixel format", v.pix_fmt); kv("Bit depth", v.bit_depth ? `${v.bit_depth}-bit${v.float ? " float" : ""}` : null); kv("Chroma", v.chroma); kv("Alpha", v.alpha ? "yes" : "no");
    kv("Primaries", v.color_primaries || "unspecified"); kv("Transfer", v.color_trc || "unspecified"); kv("Matrix", v.color_space || "unspecified"); kv("Range", v.color_range || "unspecified");
    kv("Bit rate", v.bit_rate ? `${(v.bit_rate / 1e6).toFixed(1)} Mb/s` : null);
  });
  (m.audio_streams || []).forEach((s) => { sec(`Audio stream #${s.index}`); kv("Codec", s.codec_long || s.codec); kv("Layout", `${s.layout} · ${s.channels} ch`); kv("Sample rate", s.sample_rate ? `${s.sample_rate} Hz` : null); kv("Sample format", `${s.sample_fmt || ""}${s.bit_depth ? ` · ${s.bit_depth}-bit` : ""}`); kv("Language", s.language); });
  if (m.analysis) { sec("Analysis"); const L = m.analysis.loudness || {}; kv("Integrated", L.integrated != null ? `${L.integrated} LUFS` : null); kv("LRA", L.lra != null ? `${L.lra} LU` : null); kv("True peak", L.true_peak != null ? `${L.true_peak} dBTP` : null); kv("Decoded", m.analysis.analysed_seconds ? `${m.analysis.analysed_seconds.toFixed(2)} s` : null); kv("Decoder errors", m.analysis.decode_error_count); }
  if (a.kind === "sequence" && a.seq) {
    const s = a.seq, h = (m.header || {});
    sec("Sequence"); kv("Pattern", s.pattern); kv("printf", s.printf); kv("Folder", s.dir); kv("Range", `${s.first}–${s.last}`); kv("Frames present", `${s.count} of ${s.expected}`); kv("Padding", s.padding || "none");
    kv("Header", h.format ? `${h.format} · ${h.width}×${h.height} · ${h.bit_depth}-bit${h.float ? " float" : ""} · ${h.channels} ch${h.compression ? " · " + h.compression : ""}` : null);
    if (h.channel_names) kv("Channels", h.channel_names.join(", "));
  }
  if (m.format_tags && Object.keys(m.format_tags).length) { sec("Container tags"); Object.entries(m.format_tags).slice(0, 30).forEach(([k, v]) => kv(k, v)); }
  return `<div class="kv">${rows.join("")}</div>`;
}
function paneFrames() {
  const s = S.asset.seq; if (!s) return "";
  const bad = (s.frames || []).filter((r) => r[3] && r[3] !== "ok");
  const label = { bad: "Unreadable / zero-byte", odd: "Wrong dimensions", rep: "Repeats previous frame", small: "Unusually small", dup: "Duplicate number" };
  return `<div class="group-h"><h3>Frames needing attention (${bad.length + s.missing_count})</h3></div>
    ${s.missing.map(([x, y]) => `<div class="fd" style="grid-template-columns:auto 1fr"><span class="chip fail">Missing</span><span class="pos mono" data-seek="${x - s.first}">${x}${y > x ? "–" + y : ""} <span class="muted">(${y - x + 1})</span></span></div>`).join("")}
    ${bad.map((r) => `<div class="fd" style="grid-template-columns:auto 1fr"><span class="chip ${r[3] === "rep" || r[3] === "small" ? "warning" : "fail"}">${label[r[3]] || r[3]}</span><span><span class="pos mono" data-seek="${r[0] - s.first}">${r[0]}</span> <span class="mono muted" style="font-size:11px">${esc(r[1])} · ${fmtBytes(r[2])}${r[4] ? ` · ${r[4]}×${r[5]}` : ""}</span></span></div>`).join("")}
    ${!bad.length && !s.missing_count ? `<div class="empty">All frames present and readable.</div>` : ""}`;
}
function bindPane() {
  const el = $("#rpane");
  $$("[data-seek]", el).forEach((x) => x.onclick = () => x.dataset.seek !== "" && seek(+x.dataset.seek));
  $$(".fd[data-i]", el).forEach((row) => row.addEventListener("mousedown", (e) => { if (e.target.closest("button,textarea,.pos")) return; S.ff = +row.dataset.i; $$(".fd.focus", el).forEach((r) => r.classList.remove("focus")); row.classList.add("focus"); }));
  $$("[data-bulk]", el).forEach((b) => b.onclick = async () => {
    const open = S.asset.findings.filter((f) => f.kind === "detected" && f.auto_result !== "pass" && !f.reviewer_status);
    if (!confirm(`Mark ${open.length} open detection(s) as ${RESULT_LABEL[b.dataset.bulk]}?`)) return;
    try { for (const f of open) await api("/api/finding/" + f.id, { body: { reviewer_status: b.dataset.bulk } }); await refreshAsset(); } catch (e) { fail(e); }
  });
  $$("[data-dec]", el).forEach((b) => b.onclick = () => { const row = b.closest(".fd"); S.ff = +row.dataset.i; decide(+row.dataset.fid, b.dataset.dec || null); });
  $$("[data-fnote]", el).forEach((t) => t.addEventListener("change", async () => { const fid = +t.closest(".fd").dataset.fid; try { await api("/api/finding/" + fid, { body: { reviewer_note: t.value } }); const f = S.asset.findings.find((x) => x.id === fid); if (f) f.reviewer_note = t.value; toast("Note saved"); } catch (e) { fail(e); } }));
  if ($("#nf-save")) {
    const save = async () => {
      const text = $("#nf-text").value.trim(); if (!text) return toast("Write something first", true);
      const d = S.noteDraft || { frame: S.frame }, a = S.asset;
      const useRange = $("#nf-range") && $("#nf-range").checked;
      const body = { asset_id: a.id, kind: "note", frame: useRange ? a.in_frame : d.frame, frame_out: useRange ? a.out_frame : null, severity: $("#nf-sev").value, text };
      try { if (d.id) await api("/api/note/" + d.id, { body }); else await api("/api/notes", { body }); S.noteDraft = null; if (iinaLive()) iinaCmd({ cmd: "osd", text: "Note saved" }); await refreshAsset(); } catch (e) { fail(e); }
    };
    $("#nf-save").onclick = save;
    $("#nf-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") { S.noteDraft = null; e.target.blur(); renderPane(); } });
    if ($("#nf-cancel")) $("#nf-cancel").onclick = () => { S.noteDraft = null; renderPane(); };
  }
  $$("[data-nedit]", el).forEach((b) => b.onclick = () => { const n = S.asset.notes.find((x) => x.id === +b.dataset.nedit); S.noteDraft = { ...n }; renderPane(); $("#nf-text").focus(); });
  $$("[data-ndel]", el).forEach((b) => b.onclick = async () => { if (!confirm("Delete this note?")) return; await api("/api/note/" + b.dataset.ndel, { method: "DELETE" }); await refreshAsset(); });
  if ($("#nf-iinfo")) $("#nf-iinfo").onclick = async () => {
    const a = S.asset;
    if (!confirm(`Write this asset's notes and IINfo markers to "${a.name}.iinfo.json" beside the media?\n\nIINfo then shows them on its timeline in IINA. Markers already in IINfo are kept.`)) return;
    try {
      const r = await api(`/api/asset/${a.id}/iinfo`, { body: {} });
      toast(`${r.imported ? `Added ${r.imported} IINfo marker${r.imported === 1 ? "" : "s"} · ` : ""}Wrote ${r.events} marker${r.events === 1 ? "" : "s"} for IINfo`);
      await refreshAsset();
    } catch (e) { fail(e); }
  };
}
async function decide(fid, status) {
  try {
    const r = await api("/api/finding/" + fid, { body: { reviewer_status: status } });
    const f = S.asset.findings.find((x) => x.id === fid);
    if (f) { f.reviewer_status = status; f.reviewed_by = S.st.project.settings.reviewer || "Reviewer"; f.reviewed_at = Date.now() / 1000; }
    S.asset.qc_result = r.qc_result;
    const i = S.assets.findIndex((x) => x.id === S.asset.id); if (i >= 0) { S.assets[i].qc_result = r.qc_result; S.assets[i].open_detections = S.asset.findings.filter((x) => x.kind === "detected" && ["warning", "fail"].includes(x.auto_result) && !x.reviewer_status).length; }
    const head = $(".asset-head .chip"); if (head) { head.className = "chip " + r.qc_result; head.textContent = RESULT_LABEL[r.qc_result]; }
    renderTabs(); renderPane(); renderTimeline();
    const el = $(`.fd[data-fid="${fid}"]`); if (el) el.scrollIntoView({ block: "nearest" });
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ presets
// [key, label, type, extra]. Types: pick = multi-select chips from the server vocabulary (extra = vocab
// field) plus "other" values; choice = one value with suggestions (extra = vocab field); int / num / tc /
// regex = validated text (extra = example); bool / alpha / req = fixed choices.
const PRESET_FIELDS = {
  video: [["containers", "Containers", "pick", "containers"], ["codecs", "Codecs", "pick", "codecs"], ["profiles", "Profiles", "pick", "profiles"], ["width", "Width", "int", "1920"], ["height", "Height", "int", "1080"],
    ["fps", "Frame rates", "pick", "fps"], ["progressive", "Progressive only", "bool"], ["bit_depth_min", "Min bit depth", "int", "10"], ["chroma", "Chroma", "pick", "chroma"],
    ["alpha", "Alpha", "alpha"], ["color_primaries", "Primaries", "pick", "color_primaries"], ["color_trc", "Transfer", "pick", "color_trc"], ["color_space", "Matrix", "pick", "color_space"], ["par", "Pixel aspect", "choice", "par"],
    ["min_duration", "Min duration (s)", "num"], ["max_duration", "Max duration (s)", "num"], ["start_tc", "Start timecode", "tc", "10:00:00:00"]],
  audio: [["required", "Audio", "req"], ["codecs", "Codecs", "pick", "audio_codecs"], ["sample_rate", "Sample rate", "choice", "sample_rate"], ["channels", "Total channels", "int", "2"],
    ["loudness_target", "Loudness target (LUFS)", "num", "-23"], ["loudness_tolerance", "Tolerance (LU)", "num", "1"], ["true_peak_max", "Max true peak (dBTP)", "num", "-1"]],
  sequence: [["formats", "Formats", "pick", "formats"], ["width", "Width", "int"], ["height", "Height", "int"], ["bit_depth", "Bit depths", "pick", "bit_depth"], ["start_frame", "Start frame", "int", "1001"],
    ["padding", "Padding", "int", "4"], ["naming_regex", "Name prefix regex", "regex", "^[A-Za-z0-9_]+\\.$"], ["alpha", "Alpha", "alpha"]],
};
const WIDE_PICKS = new Set(["codecs", "profiles", "audio_codecs", "par"]);
// "−23" (typographic minus) and "1,920" are how people type; parseFloat would silently drop them.
const numOf = (s, int) => { let t = String(s).trim().replace(/[−–]/g, "-"); t = int ? t.replace(/[,\s]/g, "") : t.replace(",", "."); const m = t.match(/^[+-]?\d+(\.\d+)?/); return m ? (int ? parseInt(m[0]) : parseFloat(m[0])) : NaN; };
function presetFieldError(t, v) {
  if (v === "") return "";
  if (t === "int") { const n = numOf(v, true); return isNaN(n) || n < 0 ? "Whole number" : ""; }
  if (t === "num") return isNaN(numOf(v)) ? "Number, e.g. -23" : "";
  if (t === "tc") return /^\d{1,2}[:;.]\d{2}[:;.]\d{2}[:;.]\d{2,3}$/.test(v) ? "" : "HH:MM:SS:FF (; for drop-frame)";
  if (t === "regex") { try { new RegExp(v); return ""; } catch (e) { return "Not a valid regex"; } }
  return "";
}
async function viewPresets() {
  const r = await api("/api/presets"); S.presets = r.presets; const V = r.vocab;
  if (!S.presetSel || !S.presets.find((p) => p.id === S.presetSel)) S.presetSel = r.active;
  const p = S.presets.find((x) => x.id === S.presetSel) || S.presets[0];
  const dis = p.builtin ? "disabled" : "";
  const chip = (v, label, on, custom) => `<button type="button" class="pk${on ? " on" : ""}${custom ? " custom" : ""}" data-v="${esc(JSON.stringify(v))}" title="${esc(String(v))}" ${dis}>${esc(label)}${custom && !p.builtin ? `<span class="x">×</span>` : ""}</button>`;
  const picks = (vf, cur) => {
    const opts = V[vf] || [], known = new Set(opts.map((o) => String(o.value)));
    return opts.map((o) => chip(o.value, o.label, cur.some((c) => String(c) === String(o.value)), false)).join("") +
      cur.filter((c) => !known.has(String(c))).map((c) => chip(c, String(c), true, true)).join("");
  };
  const input = (sec, [k, l, t, x]) => {
    const cur = (p[sec] || {})[k], id = `pf-${sec}-${k}`;
    if (t === "bool") return `<label class="check"><input type="checkbox" id="${id}" ${cur ? "checked" : ""} ${dis}> ${l}</label>`;
    if (t === "alpha") return `<div class="field"><label>${l}</label><select id="${id}" ${dis}>${[["any", "Any"], ["required", "Required"], ["forbidden", "Not allowed"]].map(([v, y]) => `<option value="${v}" ${(cur || "any") === v ? "selected" : ""}>${y}</option>`).join("")}</select></div>`;
    if (t === "req") return `<div class="field"><label>${l}</label><select id="${id}" ${dis}><option value="" ${cur == null ? "selected" : ""}>Not checked</option><option value="true" ${cur === true ? "selected" : ""}>Required</option><option value="false" ${cur === false ? "selected" : ""}>Optional</option></select></div>`;
    if (t === "pick") return `<div class="field${WIDE_PICKS.has(x) ? " wide" : ""}"><label>${l}</label>
      <div class="picks" id="${id}" data-vocab="${x}">${picks(x, Array.isArray(cur) ? cur : cur == null ? [] : [cur])}${p.builtin ? "" : `<input type="text" class="pk-add" placeholder="+ other" title="Add a value that isn't listed, then press Enter">`}</div></div>`;
    if (t === "choice") return `<div class="field"><label>${l}</label><input type="text" id="${id}" list="dl-${x}" value="${esc(cur ?? "")}" placeholder="not checked" autocomplete="off" ${dis}>
      <datalist id="dl-${x}">${(V[x] || []).map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("")}</datalist></div>`;
    return `<div class="field"><label>${l}</label><input type="text" id="${id}" data-t="${t}" value="${esc(cur ?? "")}" placeholder="${esc(x ? "not checked · e.g. " + x : "not checked")}" ${dis}><span class="hint err" hidden></span></div>`;
  };
  $("#view").innerHTML = `<div class="page"><div class="grid" style="grid-template-columns: 300px minmax(0,1fr)">
    <div class="card"><div class="hd"><h2>Delivery presets</h2><span class="spacer"></span><button class="btn sm" id="pr-new">${icon("plus")} New</button></div><div class="bd flush recent">
      ${S.presets.map((x) => `<div data-p="${esc(x.id)}" style="${x.id === p.id ? "background:var(--sel)" : ""}"><div class="row"><b style="font-weight:500">${esc(x.name)}</b>${x.id === r.active ? `<span class="chip pass">Active</span>` : ""}</div><span class="faint" style="font-size:11px">${x.builtin ? "Built-in" : "Custom"}</span></div>`).join("")}</div></div>
    <div class="card"><div class="hd"><h2>${esc(p.name)}</h2><span class="spacer"></span>
        ${p.id !== r.active ? `<button class="btn sm primary" id="pr-activate">Use for this project</button>` : `<span class="chip pass">Active for this project</span>`}
        <button class="btn sm" id="pr-dup">Duplicate</button>${p.builtin ? "" : `<button class="btn sm danger" id="pr-del">Delete</button>`}</div>
      <div class="bd" style="display:flex;flex-direction:column;gap:14px">
        ${p.builtin ? `<div class="banner">Built-in presets are read-only. Duplicate this preset to customise it.</div>` : ""}
        <div class="form-grid" style="grid-template-columns: 1fr 2fr"><div class="field"><label>Name</label><input type="text" id="pf-name" value="${esc(p.name)}" ${dis}></div>
          <div class="field"><label>Description</label><input type="text" id="pf-desc" value="${esc(p.description || "")}" ${dis}></div></div>
        <fieldset><legend>Video</legend><div class="form-grid">${PRESET_FIELDS.video.map((f) => input("video", f)).join("")}</div></fieldset>
        <fieldset><legend>Audio</legend><div class="form-grid">${PRESET_FIELDS.audio.map((f) => input("audio", f)).join("")}</div></fieldset>
        <fieldset><legend>Image sequences</legend><div class="form-grid">${PRESET_FIELDS.sequence.map((f) => input("sequence", f)).join("")}</div></fieldset>
        <p class="faint" style="margin:0;font-size:12px">Leave a field empty, or select nothing, to skip that check. Several selections mean "any of". Values are matched loosely, so "ProRes 422 HQ", "Rec.709", "23.98" or "48k" all work in "+ other" and suggestion boxes. Hover an option to see the name ffprobe reports.</p>
        ${p.builtin ? "" : `<div><button class="btn primary" id="pr-save">Save preset</button></div>`}
      </div></div></div></div>`;
  $$("[data-p]").forEach((d) => d.onclick = () => { S.presetSel = d.dataset.p; viewPresets(); });
  // Profiles: show only those belonging to the selected codecs (all when none are selected).
  const filterProfiles = () => {
    const box = $("#pf-video-profiles"); if (!box) return;
    const codecs = [...$$("#pf-video-codecs .pk.on")].map((b) => JSON.parse(b.dataset.v));
    const allowed = new Set(codecs.flatMap((c) => V.codec_profiles[c] || []));
    const any = !codecs.length || codecs.some((c) => !V.codec_profiles[c]);
    box.querySelectorAll(".pk:not(.custom)").forEach((b) => { b.hidden = !any && !allowed.has(JSON.parse(b.dataset.v)) && !b.classList.contains("on"); });
  };
  $$(".picks").forEach((box) => {
    box.addEventListener("click", (e) => {
      const b = e.target.closest(".pk"); if (!b || b.disabled) return;
      if (b.classList.contains("custom")) b.remove(); else b.classList.toggle("on");
      if (box.id === "pf-video-codecs") filterProfiles();
    });
    const add = box.querySelector(".pk-add"); if (!add) return;
    const commit = () => {
      for (const v of add.value.split(",").map((s) => s.trim()).filter(Boolean)) {
        // Typing a listed option (by value or label) selects it instead of adding a duplicate.
        const hit = [...box.querySelectorAll(".pk")].find((b) => [JSON.parse(b.dataset.v), b.textContent.replace("×", "")].some((s) => String(s).toLowerCase() === v.toLowerCase()));
        if (hit) hit.classList.add("on"); else add.insertAdjacentHTML("beforebegin", chip(v, v, true, true));
      }
      add.value = ""; if (box.id === "pf-video-codecs") filterProfiles();
    };
    add.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); } });
    add.addEventListener("blur", commit);
  });
  filterProfiles();
  const validate = (el) => { const msg = presetFieldError(el.dataset.t, el.value.trim()); el.classList.toggle("bad", !!msg); const h = el.nextElementSibling; if (h) { h.hidden = !msg; h.textContent = msg; } return !msg; };
  $$("[data-t]").forEach((el) => { el.addEventListener("input", () => validate(el)); validate(el); });
  const collect = () => {
    const out = { id: p.builtin ? null : p.id, name: $("#pf-name").value.trim() || "Custom preset", description: $("#pf-desc").value.trim() };
    const bad = [...$$("[data-t]")].filter((el) => !validate(el));
    if (bad.length && !p.builtin) { bad[0].focus(); throw new Error("Fix the highlighted fields before saving."); }
    for (const [sec, fields] of Object.entries(PRESET_FIELDS)) {
      const o = {};
      for (const [k, , t] of fields) {
        const el = $(`#pf-${sec}-${k}`); if (!el) continue;
        if (t === "bool") { if (el.checked) o[k] = true; continue; }
        if (t === "pick") { const v = [...el.querySelectorAll(".pk.on")].map((b) => JSON.parse(b.dataset.v)); if (v.length) o[k] = v; continue; }
        const v = el.value.trim(); if (v === "") continue;
        if (t === "int") o[k] = numOf(v, true);
        else if (t === "num") o[k] = numOf(v);
        else if (t === "alpha") { if (v !== "any") o[k] = v; }
        else if (t === "req") o[k] = v === "true";
        else o[k] = v;  // choice / tc / regex: the server normalises and validates
      }
      if (Object.keys(o).length) out[sec] = o;
    }
    return out;
  };
  const saved = (res) => (res.warnings || []).forEach((w) => toast(w, true, 8000));
  const activate = async (id) => {
    await api("/api/settings", { body: { preset_id: id } }); await refreshState();
    if (S.assets.length && confirm("Re-evaluate all assets against this preset now? (Uses stored measurements; no re-decode.)")) { await api("/api/recheck", { body: { ids: [], reanalyse: false } }); lastJobState = "running"; pollJob(); }
    viewPresets();
  };
  if ($("#pr-activate")) $("#pr-activate").onclick = () => activate(p.id).catch(fail);
  $("#pr-dup").onclick = async () => { try { const c = p.builtin ? { name: p.name, description: p.description } : collect(); c.id = null; c.name += " (copy)"; for (const k of ["video", "audio", "sequence"]) if (p.builtin && p[k]) c[k] = p[k]; const r2 = await api("/api/presets", { body: c }); saved(r2); S.presetSel = r2.id; viewPresets(); } catch (e) { fail(e); } };
  if ($("#pr-del")) $("#pr-del").onclick = async () => { if (!confirm("Delete this preset?")) return; await api("/api/preset/" + p.id, { method: "DELETE" }); S.presetSel = null; await refreshState(); viewPresets(); };
  if ($("#pr-save")) $("#pr-save").onclick = async () => { try { saved(await api("/api/presets", { body: collect() })); toast("Preset saved"); await refreshState(); if (p.id === r.active && confirm("This is the active preset. Re-evaluate all assets now?")) { await api("/api/recheck", { body: { ids: [], reanalyse: false } }); lastJobState = "running"; pollJob(); } viewPresets(); } catch (e) { fail(e); } };
  $("#pr-new").onclick = async () => { try { const r2 = await api("/api/presets", { body: { name: "New delivery preset", description: "" } }); S.presetSel = r2.id; viewPresets(); } catch (e) { fail(e); } };
}

// ------------------------------------------------------------------ reports
async function viewReports() {
  if (!S.assets.length) await loadAssets();
  S.exports = await api("/api/exports");
  const filtered = filteredAssets(), sel = S.exportIds || [...S.sel];
  const so = S.st.project.signoff;
  const s = S.st.project.settings;
  $("#view").innerHTML = `<div class="page"><div class="grid" style="grid-template-columns: minmax(0,1fr) minmax(0,1fr)">
    <div class="card"><div class="hd"><h2>Export batch report</h2></div><div class="bd" style="display:flex;flex-direction:column;gap:12px">
      <div class="field"><label>Scope</label><div class="seg" id="ex-scope">
        <button data-sc="all" class="on">All assets <span class="n">${S.assets.length}</span></button>
        <button data-sc="filtered">Current filter <span class="n">${filtered.length}</span></button>
        <button data-sc="selected" ${sel.length ? "" : "disabled"}>Selected <span class="n">${sel.length}</span></button></div></div>
      <div class="row"><button class="btn primary" data-ex="pdf">${icon("down")} PDF report</button><button class="btn" data-ex="csv">${icon("down")} CSV</button><button class="btn" data-ex="json">${icon("down")} JSON</button></div>
      <p class="muted" style="margin:0;font-size:12px">Reports include file paths, metadata, every check with its reason, automated detections with reviewer decisions, reviewer notes and sign-off status. The PDF separates measured results, automated detections and visual observations. Files are written to the project's <span class="mono">exports</span> folder.</p>
    </div></div>
    <div class="card"><div class="hd"><h2>Batch sign-off</h2></div><div class="bd" style="display:flex;flex-direction:column;gap:10px">
      ${so ? `<div class="row"><span class="chip ${so.status === "rejected" ? "fail" : so.status === "approved" ? "pass" : "warning"}">${esc(signoffLabel(so.status))}</span><span>${esc(so.by)}</span><span class="muted">${fmtTime(so.at)}</span></div>` : ""}
      <div class="form-grid" style="grid-template-columns:1fr 1fr"><div class="field"><label>Decision</label><select id="so-status"><option value="approved">Approved for delivery</option><option value="approved_notes">Approved with notes</option><option value="rejected">Rejected — redeliver</option></select></div>
      <div class="field"><label>Signed by</label><input type="text" id="so-by" value="${esc((so && so.by) || s.reviewer || "")}"></div></div>
      <div class="field"><label>Note</label><textarea id="so-note" rows="2">${esc((so && so.note) || "")}</textarea></div>
      <div class="row"><button class="btn primary" id="so-save">Record sign-off</button>${so ? `<button class="btn ghost" id="so-clear">Clear</button>` : ""}<span class="spacer"></span><span class="muted" style="font-size:12px" id="so-hint"></span></div>
    </div></div></div>
    <div class="card"><div class="hd"><h2>Exports</h2><span class="spacer"></span><span class="mono faint" style="font-size:11px">${esc(S.st.project.folder)}/exports</span></div><div class="bd flush">
      ${S.exports.length ? `<table class="tbl"><tbody>${S.exports.map((x) => `<tr><td>${esc(x.name)}</td><td class="mono muted">${fmtBytes(x.size)}</td><td class="muted">${fmtTime(x.mtime)}</td><td class="r"><a class="btn sm" href="${x.url}" target="_blank">Open</a> <a class="btn sm" href="${x.url}?dl=1">Download</a>${S.st.platform === "Darwin" ? ` <button class="btn sm ghost" data-reveal="${esc(S.st.project.folder + "/exports/" + x.name)}">Reveal</button>` : ""}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No exports yet.</div>`}</div></div></div>`;
  let scope = sel.length && S.exportIds ? "selected" : "all";
  const setScope = (k) => { scope = k; $$("#ex-scope button").forEach((b) => b.classList.toggle("on", b.dataset.sc === k)); };
  setScope(scope);
  $$("#ex-scope button").forEach((b) => b.onclick = () => setScope(b.dataset.sc));
  $$("[data-ex]").forEach((b) => b.onclick = async () => {
    const ids = scope === "all" ? null : scope === "filtered" ? filtered.map((a) => a.id) : sel;
    b.disabled = true; const old = b.innerHTML; b.textContent = "Exporting…";
    try { const r = await api("/api/export", { body: { format: b.dataset.ex, ids } }); toast(`Exported ${r.name}`); window.open(r.url, "_blank"); S.exportIds = null; viewReports(); }
    catch (e) { fail(e); b.disabled = false; b.innerHTML = old; }
  });
  $$("[data-reveal]").forEach((b) => b.onclick = () => api("/api/reveal", { body: { path: b.dataset.reveal } }));
  const unrev = S.assets.filter((a) => !["approved", "approved_notes", "rejected"].includes(a.review_status)).length;
  $("#so-hint").textContent = unrev ? `${unrev} asset(s) not yet signed off individually` : "All assets signed off";
  if (so) $("#so-status").value = so.status;
  $("#so-save").onclick = async () => { await api("/api/signoff", { body: { status: $("#so-status").value, by: $("#so-by").value, note: $("#so-note").value } }); await refreshState(); toast("Sign-off recorded"); viewReports(); };
  if ($("#so-clear")) $("#so-clear").onclick = async () => { await api("/api/signoff", { body: { status: "clear" } }); await refreshState(); viewReports(); };
}

// ------------------------------------------------------------------ settings
async function viewSettings() {
  await refreshState();
  const s = S.st.project.settings, an = s.analysis, ii = s.iina, env = S.st.iina, t = S.st.tools;
  const num = (k, l, step, hint) => `<div class="field"><label>${l}</label><input type="number" step="${step}" data-an="${k}" value="${esc(an[k])}">${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
  $("#view").innerHTML = `<div class="page" style="max-width:1100px">
    <div class="card"><div class="hd"><h2>Project</h2></div><div class="bd form-grid">
      <div class="field"><label>Project name</label><input type="text" id="s-name" value="${esc(s.project_name)}"></div>
      <div class="field"><label>Client</label><input type="text" id="s-client" value="${esc(s.client)}"></div>
      <div class="field"><label>Reviewer (your name)</label><input type="text" id="s-rev" value="${esc(s.reviewer)}"><span class="hint">Stamped on decisions, notes and sign-offs.</span></div>
      <div class="field"><label>Project folder</label><div class="mono" style="font-size:11.5px;padding-top:5px;word-break:break-all">${esc(S.st.project.folder)}</div></div>
      <div class="field"><label>Appearance</label><select id="s-theme"><option value="system">Match system</option><option value="light">Light</option><option value="dark">Dark</option></select></div>
      <div class="field"><label>&nbsp;</label><button class="btn" id="s-close">Close project</button></div>
    </div></div>
    <div class="card"><div class="hd"><h2>IINA integration</h2><span class="spacer"></span><span class="chip ${env.available ? "pass" : "warning"}">${env.available ? "Available" + (env.version ? " · v" + esc(env.version) : "") : "Unavailable"}</span></div><div class="bd" style="display:flex;flex-direction:column;gap:12px">
      ${env.reason ? `<div class="banner warn">${esc(env.reason)}</div>` : ""}
      <div class="form-grid">
        <div class="field" style="grid-column: span 2"><label>iina-cli path</label><input type="text" id="s-cli" class="mono" value="${esc(ii.cli_path)}"><span class="hint">Default: /Applications/IINA.app/Contents/MacOS/iina-cli · found: ${esc(env.cli || "not found")}</span></div>
        <div class="field"><label>Control socket</label><select id="s-mode"><option value="per_session" ${ii.socket_mode !== "fixed" ? "selected" : ""}>Per session (automatic)</option><option value="fixed" ${ii.socket_mode === "fixed" ? "selected" : ""}>Fixed path (set in IINA)</option></select></div>
        <div class="field"><label>Fixed socket path</label><input type="text" id="s-sock" class="mono" value="${esc(ii.fixed_socket)}"></div>
        <div class="field"><label>Sequence playback fps</label><input type="number" id="s-sfps" value="${esc(ii.sequence_fps)}"><span class="hint">Image sequences carry no frame rate.</span></div>
        <div class="field" style="grid-column: span 2"><label>Extra iina-cli arguments</label><input type="text" id="s-extra" class="mono" value="${esc((ii.extra_args || []).join(" "))}" placeholder="e.g. --separate-windows"></div>
      </div>
      <details><summary class="muted" style="cursor:pointer">How the IINA connection works</summary><div class="muted" style="font-size:12.5px;line-height:1.55;margin-top:8px">
        The app launches files with <span class="mono">iina-cli</span>, passing mpv options with the <span class="mono">--mpv-</span> prefix: a start position, <span class="mono">keep-open</span>, and <span class="mono">input-ipc-server</span> so IINA's mpv core opens a JSON control socket. Through that socket the app reads the playback position and frame, steps frames, seeks to exact frames and sets A-B loops from your in/out points.<br><br>
        If IINA opens the file but the app reports that the socket did not appear, use <b>fixed</b> mode: in IINA ▸ Settings ▸ Advanced, turn on “Enable advanced settings” and add the mpv option <span class="mono">input-ipc-server</span> = <span class="mono">${esc(ii.fixed_socket)}</span>, then restart IINA.<br><br>
        IINA has no notion of source timecode; timecode is computed by this app from the file's metadata. The <span class="mono">iina://</span> URL scheme is not used — IINA restricted it in 1.4.3/1.4.4 for security. If IINA is unavailable, the built-in frame viewer renders exact frames with FFmpeg.</div></details>
    </div></div>
    <div class="card"><div class="hd"><h2>Analysis thresholds</h2><span class="spacer"></span><span class="muted" style="font-size:12px">Apply to the next scan or re-scan</span></div><div class="bd" style="display:flex;flex-direction:column;gap:12px">
      <div class="row"><label class="check"><input type="checkbox" data-anb="content_checks" ${an.content_checks ? "checked" : ""}> Content checks (black, freeze, flash, silence, clipping, channel drop-outs)</label>
      <label class="check"><input type="checkbox" data-anb="loudness" ${an.loudness ? "checked" : ""}> Loudness / true peak</label>
      <label class="check"><input type="checkbox" data-anb="seq_hash_duplicates" ${an.seq_hash_duplicates ? "checked" : ""}> Detect repeated sequence frames</label></div>
      <div class="form-grid">
        ${num("black_min_duration", "Black: min duration (s)", 0.01)}${num("black_pixel_threshold", "Black: pixel threshold (0–1)", 0.01)}
        ${num("freeze_min_duration", "Freeze: min duration (s)", 0.1)}${num("freeze_noise", "Freeze: noise tolerance", 0.0005)}
        ${num("flash_luma_jump", "Flash: luma jump (0–255)", 1)}${num("silence_threshold_db", "Silence: threshold (dBFS)", 1)}
        ${num("silence_min_duration", "Silence: min duration (s)", 0.1)}${num("clip_threshold_dbfs", "Clipping: threshold (dBFS)", 0.1)}
        ${num("dead_channel_db", "Dead channel: below (dB RMS)", 1)}${num("seq_size_outlier_ratio", "Sequence: small-frame ratio", 0.05, "Frame smaller than this × median")}
        ${num("max_analysis_seconds", "Decode limit (s, 0 = whole file)", 1, "Speeds up huge files; later content is not checked")}${num("workers", "Parallel files", 1)}
      </div></div></div>
    <div class="card"><div class="hd"><h2>Tools</h2></div><div class="bd flush kv">
      ${["ffprobe", "ffmpeg"].map((k) => `<div>${k}</div><div>${t[k].available ? esc(t[k].path + " — " + t[k].version) : '<span style="color:var(--fail)">not found — install FFmpeg (brew install ffmpeg)</span>'}</div>`).join("")}
      <div>IINA</div><div>${esc(env.cli || "not found")}${env.version ? " — " + esc(env.version) : ""}</div>
      <div>Platform</div><div>${esc(env.platform)}</div></div></div>
    <div class="row"><button class="btn primary" id="s-save">Save settings</button></div></div>`;
  $("#s-theme").value = prefs.theme || "system";
  $("#s-theme").onchange = (e) => { prefs.theme = e.target.value; savePrefs(); applyTheme(); };
  $("#s-close").onclick = async () => { if (!confirm("Close this project? Review data is saved.")) return; try { await api("/api/project/close", { body: {} }); location.hash = ""; location.reload(); } catch (e) { fail(e); } };
  $("#s-save").onclick = async () => {
    const analysis = {};
    $$("[data-an]").forEach((i) => { const n = parseFloat(i.value); if (Number.isFinite(n)) analysis[i.dataset.an] = n; });  // emptied box keeps the saved value
    $$("[data-anb]").forEach((i) => analysis[i.dataset.anb] = i.checked);
    analysis.workers = Math.max(1, Math.min(8, parseInt(analysis.workers) || 2));
    const body = { project_name: $("#s-name").value, client: $("#s-client").value, reviewer: $("#s-rev").value, analysis,
      iina: { cli_path: $("#s-cli").value.trim(), socket_mode: $("#s-mode").value, fixed_socket: $("#s-sock").value.trim(), sequence_fps: parseFloat($("#s-sfps").value) || 25,
        extra_args: $("#s-extra").value.trim() ? $("#s-extra").value.trim().split(/\s+/) : [] } };
    try { await api("/api/settings", { body }); toast("Settings saved"); await refreshState(); viewSettings(); } catch (e) { fail(e); }
  };
}

// ------------------------------------------------------------------ keyboard
function helpDialog() {
  const k = (keys, what) => `<div><span>${what}</span><span>${keys.map((x) => `<kbd>${x}</kbd>`).join(" ")}</span></div>`;
  modal(`<div class="hd"><b>Keyboard shortcuts</b><span style="flex:1"></span><button class="btn ghost sm" data-close>${icon("x")}</button></div><div class="bd"><div class="help-grid">
    ${k(["1", "–", "6"], "Switch view")}${k(["/"], "Search media")}${k(["↑", "↓"], "Move in list")}${k(["Enter"], "Open asset")}${k(["x"], "Select row")}${k(["?"], "This help")}
    ${k(["←", "→"], "Step one frame")}${k(["⇧←", "⇧→"], "Step one second")}${k(["Space"], "Play / pause (player or IINA)")}${k(["p"], "Frame viewer / browser player")}${k(["Home", "End"], "First / last frame")}
    ${k(["e"], "Open in IINA here")}${k(["f"], "Follow IINA on/off")}${k(["g"], "Go to timecode / frame")}${k(["i", "o"], "Set in / out")}
    ${k(["b"], "Bookmark")}${k(["n"], "Add note")}${k(["[", "]"], "Previous / next finding")}${k(["j", "k"], "Focus next / previous finding")}
    ${k(["1", "2", "3", "4"], "Pass / Issue / Warning / N/A (review)")}${k(["0"], "Clear decision")}${k([",", "."], "Previous / next asset")}${k(["a", "r"], "Approve / reject asset")}${k(["Esc"], "Back to media list")}
  </div></div>`);
}
document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable) {
    if (e.key === "Escape") e.target.blur();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (!$("#modal").hidden) { if (e.key === "Escape") closeModal(); return; }
  if (!S.st || !S.st.project) return;
  const k = e.key;
  if (k === "?") { e.preventDefault(); return helpDialog(); }
  if (k === "/") { e.preventDefault(); return $("#gsearch") && $("#gsearch").focus(); }
  const inReview = S.view === "review" || S.view === "asset";
  if (!inReview && /^[1-6]$/.test(k)) { const v = ["dashboard", "media", "review", "presets", "reports", "settings"][+k - 1]; return go("#/" + v); }
  if (S.view === "media") {
    if (k === "ArrowDown" || k === "j") { e.preventDefault(); S.cursor = Math.min(mList.length - 1, S.cursor + 1); scrollCursor(); renderMediaRows(); }
    else if (k === "ArrowUp" || k === "k") { e.preventDefault(); S.cursor = Math.max(0, S.cursor - 1); scrollCursor(); renderMediaRows(); }
    else if (k === "Enter" && mList[S.cursor]) go("#/asset/" + mList[S.cursor].id);
    else if (k === "x" && mList[S.cursor]) { const id = mList[S.cursor].id; S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id); renderMediaRows(); }
    else if (k === "e" && mList[S.cursor]) go("#/asset/" + mList[S.cursor].id);
    return;
  }
  if (!inReview || !S.asset) return;
  const fps = Math.max(1, Math.round(S.ctx.fps || 25));
  const vis = visibleFindings();
  switch (k) {
    case "ArrowLeft": e.preventDefault(); step(e.shiftKey ? -fps : -1); break;
    case "ArrowRight": e.preventDefault(); step(e.shiftKey ? fps : 1); break;
    case " ": e.preventDefault(); transport("play"); break;
    case "Home": e.preventDefault(); seek(0); break;
    case "End": e.preventDefault(); seek(S.ctx.count - 1); break;
    case "e": openInIina(); break;
    case "p": if (S.asset.kind === "video" || S.asset.kind === "sequence") setPlayerMode(!S.player); break;
    case "f": S.follow = !S.follow; toast(`Follow IINA ${S.follow ? "on" : "off"}`); renderIinaBar(); break;
    case "g": e.preventDefault(); $("#tcin").focus(); break;
    case "i": transport("in"); break;
    case "o": transport("out"); break;
    case "b": transport("bookmark"); break;
    case "n": e.preventDefault(); transport("note"); break;
    case "[": jumpFinding(-1); break;
    case "]": jumpFinding(1); break;
    case "j": case "k": {
      if (!vis.length) break;
      S.tab = "checks"; renderTabs();
      S.ff = k === "j" ? Math.min(vis.length - 1, S.ff + 1) : Math.max(0, S.ff - 1);
      renderPane(); const f = vis[S.ff]; const el = $(`.fd[data-fid="${f.id}"]`); if (el) el.scrollIntoView({ block: "nearest" });
      if (f.frame_in != null && f.check_key !== "seq.range") seek(f.frame_in);
      break;
    }
    case "1": case "2": case "3": case "4": case "0": {
      const f = vis[S.ff >= 0 ? S.ff : 0]; if (!f) break;
      S.ff = vis.indexOf(f);
      decide(f.id, { 1: "pass", 2: "issue", 3: "warning", 4: "na", 0: null }[k]);
      break;
    }
    case ",": $("#rv-prev") && $("#rv-prev").click(); break;
    case ".": $("#rv-next") && $("#rv-next").click(); break;
    case "a": setReview("approved"); break;
    case "r": setReview("rejected"); break;
    case "Escape": go("#/media"); break;
    default: return;
  }
});
function scrollCursor() { const w = $("#listwrap"); if (!w) return; const y = S.cursor * 34; if (y < w.scrollTop + 34) w.scrollTop = Math.max(0, y - 34); else if (y > w.scrollTop + w.clientHeight - 68) w.scrollTop = y - w.clientHeight + 68; }

// ------------------------------------------------------------------ go
(async () => {
  try { await refreshState(); } catch (e) { $("#view").innerHTML = `<div class="page"><div class="banner err">Cannot reach the nitpix server: ${esc(e.message)}</div></div>`; return; }
  route(); pollJob();
})();
