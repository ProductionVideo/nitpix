"""Local HTTP server: JSON API + static web UI. Binds to 127.0.0.1 only."""
from __future__ import annotations

import json
import mimetypes
import os
import platform
import re
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import demo, frames, reports, service, vocab
from .service import LIST_COLS
from .iina import IINABridge, IINAUnavailable
from .presets import builtin_by_id
from .probe import tool_versions
from .proxy import ProxyManager
from .scanner import JobManager, active_preset, all_presets
from .store import Project, recent_projects, remember_project

WEB = Path(__file__).parent / "web"




class App:
    def __init__(self):
        self.project: Project | None = None
        self.jobs: JobManager | None = None
        self.iina: IINABridge | None = None
        self.proxies: ProxyManager | None = None
        self.lock = threading.Lock()

    def open_project(self, folder: str, name: str | None = None):
        folder = os.path.expanduser(folder)
        if not folder.endswith((".nitpix", ".lhsqc")):  # .lhsqc: projects from before the rename
            folder += ".nitpix"
        with self.lock:
            if self.jobs and self.jobs.active:
                raise RuntimeError("Finish or cancel the running batch before switching projects.")
            if self.proxies:
                self.proxies.close()
            if self.project:
                self.project.close()
            p = Project(folder)
            if name:
                p.update_settings({"project_name": name})
            self.project = p
            self.jobs = JobManager(p)
            self.iina = IINABridge(p)
            self.proxies = ProxyManager(p)
            remember_project(p.folder, p.settings["project_name"])
            # Assets left mid-scan by a crash/quit go back to pending.
            p.x("UPDATE assets SET scan_state='pending' WHERE scan_state IN ('probing','analysing')")
        return p


APP = App()


def _json(v):
    return json.dumps(v, default=str)


class Handler(BaseHTTPRequestHandler):
    server_version = "nitpix/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("NITPIX_DEBUG"):
            super().log_message(fmt, *args)

    # ---- helpers ------------------------------------------------------------------
    def send(self, code=200, body=None, ctype="application/json", headers=None):
        data = body if isinstance(body, (bytes, bytearray)) else _json(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def err(self, status, msg, **extra):
        self.send(status, {"error": msg, **extra})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n) or b"{}")

    def file(self, path: Path, download=False):
        if not path.exists() or not path.is_file():
            return self.err(404, "Not found")
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix == ".js":
            ctype = "text/javascript"
        h = {"Content-Disposition": f'attachment; filename="{path.name}"'} if download else {}
        self.send(200, path.read_bytes(), ctype, h)

    def stream(self, path: Path, ctype: str):
        """Serve a large file with HTTP Range support, which <video> needs to seek."""
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range") or ""
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:  # suffix range: the last N bytes
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player moved on to another range

    def proj(self) -> Project:
        if not APP.project:
            raise LookupError("No project open")
        return APP.project

    # ---- dispatch ----------------------------------------------------------------
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        u = urlparse(self.path)
        path = unquote(u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        # Basic CSRF guard: API writes must come from our own page.
        if method != "GET" and path.startswith("/api/"):
            origin = self.headers.get("Origin") or ""
            host = self.headers.get("Host") or ""
            if origin and not origin.endswith("//" + host):
                return self.err(403, "Cross-origin request refused")
        try:
            for m, pat, fn in ROUTES:
                if m != method:
                    continue
                mm = re.fullmatch(pat, path)
                if mm:
                    return fn(self, q, *mm.groups())
            if method == "GET":
                if path == "/" or path == "/index.html":
                    return self.file(WEB / "index.html")
                if path.startswith("/static/"):
                    p = (WEB / path[len("/static/"):]).resolve()
                    if WEB.resolve() in p.parents:
                        return self.file(p)
                if path.startswith("/exports/"):
                    pr = self.proj()
                    p = pr.safe_path("exports", Path(path).name)
                    return self.file(p, download=q.get("dl") == "1")
            self.err(404, f"No route {method} {path}")
        except LookupError as e:
            self.err(409, str(e), code="no_project")
        except IINAUnavailable as e:
            self.err(503, str(e), code="iina_unavailable")
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as e:
            self.err(400, str(e))
        except Exception as e:
            traceback.print_exc()
            self.err(500, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def r_state(h, q):
    p = APP.project
    out = {"tools": tool_versions(), "recent": recent_projects(), "platform": platform.system(), "project": None}
    if p:
        job = APP.jobs.latest()
        out["project"] = {"folder": str(p.folder), "settings": p.settings, "preset": active_preset(p),
                          "signoff": p.get_meta("signoff"), "roots": [r["path"] for r in p.q("SELECT path FROM roots")]}
        out["job"] = job.as_dict() if job else None
        out["iina"] = APP.iina.environment()
    h.send(200, out)


def r_open(h, q):
    b = h.body()
    if not b.get("path"):
        raise ValueError("Choose a project folder")
    p = APP.open_project(b["path"], b.get("name"))
    h.send(200, {"folder": str(p.folder)})


def r_close(h, q):
    with APP.lock:
        if APP.jobs and APP.jobs.active:
            raise RuntimeError("Finish or cancel the running batch before closing the project.")
        if APP.iina:
            APP.iina.close()
        if APP.proxies:
            APP.proxies.close()
        if APP.project:
            APP.project.close()
        APP.project = APP.jobs = APP.iina = APP.proxies = None
    h.send(200, {"ok": True})


def r_demo(h, q):
    """Generate (if needed) and load the demo delivery."""
    loc = demo.locations()
    if not loc["delivery"].exists():
        demo.generate(loc["delivery"], log=lambda *_: None)
    p = APP.open_project(str(loc["project"]), demo.PROJECT_NAME)
    p.update_settings({"client": "Demo client", "preset_id": "lhs_hd_prores_25"})
    APP.jobs.scan_roots([str(loc["delivery"])])
    h.send(200, {"folder": str(p.folder)})


def r_assets(h, q):
    p = h.proj()
    if q.get("check"):
        rows = p.q("SELECT DISTINCT asset_id FROM findings WHERE check_key=? AND COALESCE(reviewer_status, auto_result) IN ('fail','issue','warning')", (q["check"],))
        return h.send(200, [r["asset_id"] for r in rows])
    h.send(200, service.list_assets(p))


def r_asset(h, q, aid):
    p = h.proj()
    a = service.asset_detail(p, int(aid))
    if not a:
        return h.err(404, "Asset not found")
    h.send(200, a)


def r_asset_update(h, q, aid):
    p = h.proj()
    b = h.body()
    service.update_asset(p, int(aid), b, by=b.get("by"))
    h.send(200, {"ok": True})


def r_bulk_review(h, q):
    p = h.proj()
    b = h.body()
    service.bulk_review(p, b.get("ids", []), b.get("review_status"))
    h.send(200, {"ok": True})


def r_finding(h, q, fid):
    p = h.proj()
    b = h.body()
    try:
        res = service.decide_finding(p, int(fid), status=b.get("reviewer_status", service._UNSET),
                                     note=b.get("reviewer_note", service._UNSET))
    except LookupError:
        return h.err(404, "Finding not found")
    h.send(200, {"ok": True, "qc_result": res})


def r_note_create(h, q):
    p = h.proj()
    b = h.body()
    nid, res = service.add_note(p, int(b["asset_id"]), b.get("kind", "note"), b.get("frame"), b.get("frame_out"),
                                b.get("severity", "info"), b.get("text", ""))
    h.send(200, {"id": nid, "qc_result": res})


def r_note_update(h, q, nid):
    p = h.proj()
    b = h.body()
    try:
        res = service.update_note(p, int(nid), **{k: b[k] for k in ("text", "severity", "frame", "frame_out", "kind") if k in b})
    except LookupError:
        return h.err(404, "Note not found")
    h.send(200, {"ok": True, "qc_result": res})


def r_note_delete(h, q, nid):
    service.delete_note(h.proj(), int(nid))
    h.send(200, {"ok": True})


def r_iinfo_sync(h, q, aid):
    h.send(200, {"ok": True, **service.iinfo_sync(h.proj(), int(aid))})


def r_scan(h, q):
    b = h.body()
    roots = b.get("roots") or []
    if isinstance(roots, str):
        roots = [r.strip() for r in roots.splitlines() if r.strip()]
    if not roots:
        raise ValueError("Add at least one file or folder path")
    job = h.proj() and APP.jobs.scan_roots(roots, force=bool(b.get("force")))
    h.send(200, job.as_dict())


def r_recheck(h, q):
    h.proj()
    b = h.body()
    ids = [int(i) for i in b.get("ids", [])]
    if not ids:
        ids = [r["id"] for r in APP.project.q("SELECT id FROM assets")]
    h.send(200, APP.jobs.recheck(ids, reanalyse=b.get("reanalyse", True)).as_dict())


def r_remove(h, q):
    service.remove_assets(h.proj(), h.body().get("ids", []))
    h.send(200, {"ok": True})


def r_job(h, q):
    h.proj()
    j = APP.jobs.latest()
    h.send(200, j.as_dict() if j else None)


def r_job_ctl(h, q, action):
    h.proj()
    getattr(APP.jobs, action)()
    j = APP.jobs.latest()
    h.send(200, j.as_dict() if j else None)


def r_dashboard(h, q):
    out = service.dashboard(h.proj())
    j = APP.jobs.latest()
    out["job"] = j.as_dict() if j else None
    h.send(200, out)


def r_presets(h, q):
    h.send(200, service.presets(h.proj()))


def r_preset_save(h, q):
    pid, warnings = service.save_preset(h.proj(), h.body())
    h.send(200, {"id": pid, "warnings": warnings})


def r_preset_delete(h, q, pid):
    service.delete_preset(h.proj(), pid)
    h.send(200, {"ok": True})


def r_settings(h, q):
    h.send(200, service.update_settings(h.proj(), h.body()))


def r_signoff(h, q):
    b = h.body()
    h.send(200, service.signoff(h.proj(), b.get("status"), b.get("by"), b.get("note", "")))


def r_frame(h, q):
    p = h.proj()
    a = p.asset(int(q["asset"]))
    try:
        path = frames.grab(p, a, int(q.get("frame", 0)), int(q.get("w", 1280)))
    except frames.FrameError as e:
        return h.err(422, str(e))
    h.file(Path(path))


def r_thumb(h, q):
    p = h.proj()
    a = p.asset(int(q["asset"]))
    try:
        path = frames.thumb(p, a)
    except Exception as e:
        return h.err(422, str(e))
    h.file(Path(path))


def _proxies():
    if not APP.proxies:
        raise LookupError("No project open")
    return APP.proxies


def r_proxy_state(h, q, aid):
    a = h.proj().asset(int(aid))
    if not a:
        return h.err(404, "Asset not found")
    h.send(200, _proxies().state(a))


def r_proxy_make(h, q, aid):
    a = h.proj().asset(int(aid))
    if not a:
        return h.err(404, "Asset not found")
    h.send(200, _proxies().request(a))


def r_proxy_file(h, q, aid, name):
    a = h.proj().asset(int(aid))
    if not a:
        return h.err(404, "Asset not found")
    p = _proxies().path(a)
    if p.name != name or not p.exists():
        return h.err(404, "Proxy not ready")
    h.stream(p, "video/mp4")


def r_iina_open(h, q):
    p = h.proj()
    b = h.body()
    a = p.asset(int(b["asset_id"]))
    st = APP.iina.open(a, int(b.get("frame") or 0))
    if a.get("in_frame") is not None or a.get("out_frame") is not None:
        try:
            APP.iina.set_loop(a.get("in_frame"), a.get("out_frame")) if b.get("loop") else None
        except Exception:
            pass
    h.send(200, st)


def r_iina_state(h, q):
    h.proj()
    h.send(200, APP.iina.state())


def r_iina_cmd(h, q):
    h.proj()
    b = h.body()
    c = b.get("cmd")
    br = APP.iina
    if c == "seek":
        br.seek_frame(int(b["frame"]))
    elif c == "step":
        br.step(int(b.get("n", 1)))
    elif c == "pause":
        br.set_pause(b.get("paused"))
    elif c == "loop":
        br.set_loop(b.get("in"), b.get("out"))
    elif c == "osd":
        br.osd(str(b.get("text", ""))[:200])
    elif c == "close":
        br.close()
    else:
        raise ValueError("Unknown IINA command")
    h.send(200, br.state())


def r_export(h, q):
    p = h.proj()
    b = h.body()
    fmt = b.get("format", "pdf")
    ids = b.get("ids")                       # absent / null: every asset; a list (even empty) is the scope
    fn = {"csv": reports.export_csv, "json": reports.export_json, "pdf": reports.export_pdf}.get(fmt)
    if not fn:
        raise ValueError("Unknown export format")
    path = fn(p, ids)
    h.send(200, {"path": path, "url": "/exports/" + Path(path).name, "name": Path(path).name})


def r_exports(h, q):
    p = h.proj()
    d = p.safe_path("exports")
    items = sorted(d.glob("*.*"), key=lambda x: x.stat().st_mtime, reverse=True)
    h.send(200, [{"name": x.name, "url": "/exports/" + x.name, "size": x.stat().st_size,
                  "mtime": x.stat().st_mtime} for x in items[:50]])


def r_pick(h, q):
    """Native macOS chooser via AppleScript; elsewhere the UI falls back to its own browser."""
    b = h.body()
    if platform.system() != "Darwin":
        return h.send(200, {"unsupported": True})
    what = b.get("what", "folder")
    if what == "folder":
        script = 'set f to choose folder with prompt "Choose a delivery folder to scan" with multiple selections allowed\n' \
                 'set out to ""\nrepeat with i in f\nset out to out & POSIX path of i & linefeed\nend repeat\nreturn out'
    elif what == "project":
        script = 'return POSIX path of (choose folder with prompt "Choose or create a folder for QC projects")'
    else:
        script = 'set f to choose file with prompt "Choose media files" with multiple selections allowed\n' \
                 'set out to ""\nrepeat with i in f\nset out to out & POSIX path of i & linefeed\nend repeat\nreturn out'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode:
        return h.send(200, {"cancelled": True})
    h.send(200, {"paths": [x for x in r.stdout.strip().splitlines() if x]})


def r_ls(h, q):
    path = os.path.expanduser(q.get("path") or "~")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    entries = []
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if e.name.startswith("."):
                continue
            try:
                isdir = e.is_dir()
            except OSError:
                continue
            entries.append({"name": e.name, "dir": isdir, "path": e.path})
    except PermissionError:
        raise PermissionError(f"No permission to read {path}")
    h.send(200, {"path": path, "parent": os.path.dirname(path), "entries": entries[:2000]})


def r_reveal(h, q):
    b = h.body()
    path = b.get("path")
    if platform.system() == "Darwin" and path and os.path.exists(path):
        subprocess.run(["open", "-R", path])
        return h.send(200, {"ok": True})
    h.send(200, {"ok": False, "path": path})


ROUTES = [
    ("GET", r"/api/state", r_state),
    ("POST", r"/api/project/open", r_open),
    ("POST", r"/api/project/demo", r_demo),
    ("POST", r"/api/project/close", r_close),
    ("GET", r"/api/assets", r_assets),
    ("GET", r"/api/asset/(\d+)", r_asset),
    ("POST", r"/api/asset/(\d+)", r_asset_update),
    ("POST", r"/api/assets/review", r_bulk_review),
    ("POST", r"/api/assets/remove", r_remove),
    ("POST", r"/api/finding/(\d+)", r_finding),
    ("POST", r"/api/notes", r_note_create),
    ("POST", r"/api/note/(\d+)", r_note_update),
    ("DELETE", r"/api/note/(\d+)", r_note_delete),
    ("POST", r"/api/asset/(\d+)/iinfo", r_iinfo_sync),
    ("POST", r"/api/scan", r_scan),
    ("POST", r"/api/recheck", r_recheck),
    ("GET", r"/api/job", r_job),
    ("POST", r"/api/job/(pause|resume|cancel)", r_job_ctl),
    ("GET", r"/api/dashboard", r_dashboard),
    ("GET", r"/api/presets", r_presets),
    ("POST", r"/api/presets", r_preset_save),
    ("DELETE", r"/api/preset/([\w\-]+)", r_preset_delete),
    ("POST", r"/api/settings", r_settings),
    ("POST", r"/api/signoff", r_signoff),
    ("GET", r"/api/frame", r_frame),
    ("GET", r"/api/thumb", r_thumb),
    ("GET", r"/api/proxy/(\d+)", r_proxy_state),
    ("POST", r"/api/proxy/(\d+)", r_proxy_make),
    ("GET", r"/api/proxy/(\d+)/(a\d+_[0-9a-f]+\.mp4)", r_proxy_file),
    ("POST", r"/api/iina/open", r_iina_open),
    ("GET", r"/api/iina/state", r_iina_state),
    ("POST", r"/api/iina/cmd", r_iina_cmd),
    ("POST", r"/api/export", r_export),
    ("GET", r"/api/exports", r_exports),
    ("POST", r"/api/pick", r_pick),
    ("GET", r"/api/ls", r_ls),
    ("POST", r"/api/reveal", r_reveal),
]


def serve(port=8765, project=None, open_browser=True):
    if project:
        APP.open_project(project)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"nitpix running at {url}  (Ctrl+C to quit)")
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if APP.jobs and APP.jobs.active:
            APP.jobs.cancel()
        httpd.server_close()
