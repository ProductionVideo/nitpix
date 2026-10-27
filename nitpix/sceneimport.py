"""Stage import in a separate process, so the app stays responsive and a bad file cannot take it down.

assimp-py holds the GIL for the whole native parse (measured: a 1.4M-triangle OBJ froze the
calling process's other threads for 1.5 s), so a worker thread would freeze the UI. Instead the
app runs itself again with --scene-import, which reads the file with nitpix.scene.load_scene and
hands back the NormalizedScene (or the error) through a private temporary file.

With a project folder, the helper first keeps a copy of the model (and its sidecars) in the
project (nitpix/models.py) and imports that copy, so what is saved is exactly what was read; the
result then also carries the resource record. A copy it made for an import that then fails is
removed again. Restoring a saved setup imports the managed copy directly, without a project.

- One import at a time: starting another, or cancel(), kills the running helper, which really
  stops the native parse.
- Every import has a generation; a result for an older generation is dropped, so a slow import
  can never land on another project or replace a newer scene.
- A helper that runs longer than TIMEOUT seconds is killed and reported.
- A crash in the native importer ends the helper, not the app, and is reported as such. (Damaged
  files tried so far gave readable errors; that is not proof every file will.)
"""
from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import threading

from .scene import SceneImportError, load_scene

TIMEOUT = 300


def helper_command(path: str, out: str, project: str | None = None) -> list[str]:
    extra = ["--keep-in", project] if project else []
    if getattr(sys, "frozen", False):           # the packaged app runs itself
        return [sys.executable, "--scene-import", path, out, *extra]
    return [sys.executable, "-m", "nitpix", "--scene-import", path, out, *extra]


def run_helper(path: str, out: str, project: str | None = None) -> int:
    """The helper process: import `path` (kept in `project` first, if given), write the result to `out`."""
    resource = None
    try:
        if project:
            from .models import ModelError, manage, resolve
            try:
                resource = manage(path, project)
            except (ModelError, OSError) as e:
                raise SceneImportError(f"Could not keep a copy in the project: {e}") from None
            try:
                scene = load_scene(resolve(project, resource))
            except BaseException:
                if resource.get("created"):          # ours, and nothing uses it: take it back out
                    shutil.rmtree(os.path.dirname(resolve(project, resource)), True)
                raise
            result = {"scene": scene, "resource": resource}
        else:
            result = {"scene": load_scene(path)}
    except SceneImportError as e:
        result = {"error": str(e)}
    except Exception as e:  # report anything else rather than dying silently
        result = {"error": f"Could not read this scene ({type(e).__name__}: {e})."}
    with open(out, "wb") as fh:
        pickle.dump(result, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return 0


class Importer:
    """Runs one helper at a time. `done(generation, scene, error, resource)` is called on a
    background thread; the caller moves it to the GUI thread and compares the generation."""

    def __init__(self, done):
        self._done = done
        self._lock = threading.Lock()
        self._proc = None
        self.generation = 0

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._proc is not None

    def start(self, path: str, project: str | None = None) -> int:
        with self._lock:
            self._kill_locked()
            self.generation += 1
            gen = self.generation
            tmp = tempfile.mkdtemp(prefix="nitpix-scene-")      # private (0700)
            out = os.path.join(tmp, "scene.pickle")
            try:
                proc = subprocess.Popen(helper_command(path, out, project), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except OSError as e:
                shutil.rmtree(tmp, True)
                threading.Thread(target=self._done, args=(gen, None, f"Could not start the scene importer: {e}", None), daemon=True).start()
                return gen
            self._proc = proc
        threading.Thread(target=self._wait, args=(gen, proc, tmp, out), daemon=True).start()
        return gen

    def cancel(self):
        """Stop the running import (if any) and drop its result."""
        with self._lock:
            self._kill_locked()
            self.generation += 1

    def _kill_locked(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None

    def _wait(self, gen, proc, tmp, out):
        scene, error, resource = None, None, None
        try:
            try:
                _, err = proc.communicate(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                error = f"The import took longer than {TIMEOUT // 60} minutes and was stopped."
            else:
                if proc.returncode == 0 and os.path.exists(out):
                    with open(out, "rb") as fh:
                        r = pickle.load(fh)     # written by our own helper, in our private folder
                    scene, error, resource = r.get("scene"), r.get("error"), r.get("resource")
                elif proc.returncode < 0 and proc.returncode in (-9, -15):
                    error = "cancelled"
                else:
                    tail = (err or b"").decode(errors="replace").strip().splitlines()[-1:] or [""]
                    error = f"The scene importer stopped unexpectedly ({proc.returncode}). {tail[0]}".strip()
        except Exception as e:
            error = f"Could not read the importer's result ({e})."
        finally:
            shutil.rmtree(tmp, True)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
        self._done(gen, scene, error, resource)
