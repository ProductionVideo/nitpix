"""Model files kept inside a project (`<project>/models/`), so a Mapping setup reopens without the
folder it was built from. No Qt; runs in the import helper process (nitpix/sceneimport.py).

- A managed resource is one model file plus the sidecars it needs: for OBJ, the material
  libraries its `mtllib` lines name. Textures are not copied: Mapping never draws them. An FBX is
  copied alone (embedded data travels with it; external textures are not used).
- It is stored in `models/<id>/`, where id is the first 20 hex digits of a SHA-256 over the model's
  name and bytes and its sidecars' relative names and bytes. The same file (name and bytes) shares
  one folder; a different file with the same name, or the same bytes under another name, gets
  another, so adding one never moves or replaces a copy something else refers to. Sidecars keep their path relative to the model, so `mtllib`
  lines resolve unchanged. Sidecars outside the model's own folder, or missing, are not copied and
  are listed, never silently promised.
- The original is only read. The copy is written to a temporary folder inside `models/` and
  renamed into place when complete, so an interrupted copy never looks like a resource.
- A resource is identified for later checks by its id and the model's SHA-256 and size; where
  it came from (`origin`) is kept for information and relinking only.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile

MODELS = "models"
ID_LEN = 20


class ModelError(Exception):
    """A model that cannot be kept; the message is for the person importing it."""


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def obj_sidecars(path: str) -> tuple[list[str], list[tuple[str, str]]]:
    """(material libraries to keep, as paths relative to the OBJ; [(name, why not kept)])."""
    keep, skipped = [], []
    here = os.path.dirname(os.path.abspath(path))
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.strip()
            if not line.startswith(b"mtllib"):
                continue
            text = line[6:].strip().decode("utf-8", "replace")
            if not text:
                continue
            # One name with spaces if that file exists, else several names (the OBJ convention).
            names = [text] if os.path.isfile(os.path.join(here, text)) or " " not in text else text.split()
            for name in names:
                rel = os.path.normpath(name)
                full = os.path.normpath(os.path.join(here, rel))
                if os.path.isabs(name) or rel.startswith(os.pardir):
                    skipped.append((name, "outside the model's folder, not copied"))
                elif not os.path.isfile(full):
                    skipped.append((name, "missing next to the model"))
                elif rel not in keep:
                    keep.append(rel)
    return keep, skipped


def manage(src: str, project_folder: str) -> dict:
    """Copy `src` (and its sidecars) into the project's models folder, or find the copy already there.
    Returns the resource record: id, path (relative to the project), name, sha256, size, origin,
    sidecars, missing."""
    src = os.path.abspath(src)
    if not os.path.isfile(src):
        raise ModelError(f"File not found: {src}")
    root = os.path.realpath(os.path.join(project_folder, MODELS))
    os.makedirs(root, exist_ok=True)
    name = os.path.basename(src)
    sidecars, missing = obj_sidecars(src) if name.lower().endswith(".obj") else ([], [])
    here = os.path.dirname(src)
    main_hash = sha256(src)
    h = hashlib.sha256(main_hash.encode() + b"\0" + name.encode())
    for rel in sidecars:
        h.update(b"\0" + rel.replace(os.sep, "/").encode() + b"\0" + sha256(os.path.join(here, rel)).encode())
    rid = h.hexdigest()[:ID_LEN]
    final = os.path.join(root, rid)
    if not _inside(final, root):
        raise ModelError("Refusing to write outside the project's models folder.")
    created = not _intact(final, name, main_hash, sidecars)
    if created:
        tmp = tempfile.mkdtemp(prefix=".incoming-", dir=root)
        try:
            for rel in [name, *sidecars]:
                dst = os.path.join(tmp, rel)
                if not _inside(dst, tmp):
                    raise ModelError(f"Refusing to copy {rel} outside the models folder.")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(here, rel), dst)
            if os.path.isdir(final):                     # a damaged earlier copy of this same file: replace it
                shutil.rmtree(final)
            os.replace(tmp, final)
        except BaseException:
            shutil.rmtree(tmp, True)
            raise
    return {"id": rid, "path": f"{MODELS}/{rid}/{name}", "name": name, "sha256": main_hash,
            "size": os.path.getsize(src), "origin": src, "sidecars": [s.replace(os.sep, "/") for s in sidecars],
            "missing": [f"{n}: {why}" for n, why in missing], "created": created}


def check(project_folder: str, resource: dict) -> str | None:
    """Why the managed copy cannot be used as saved (None: it can). Reads the bytes."""
    path = resolve(project_folder, resource)
    if path is None:
        return "its stored path is not inside this project"
    if not os.path.isfile(path):
        return "the copy kept in this project is missing"
    if os.path.getsize(path) != resource.get("size") or sha256(path) != resource.get("sha256"):
        return "the copy kept in this project has changed since it was saved"
    return None


def resolve(project_folder: str, resource: dict) -> str | None:
    """The managed file's absolute path, only if it lies inside the project's models folder."""
    root = os.path.realpath(os.path.join(project_folder, MODELS))
    p = os.path.realpath(os.path.join(project_folder, str(resource.get("path", ""))))
    return p if _inside(p, root) and p != root else None


def clean_incoming(project_folder: str):
    """Remove copies interrupted before they were complete (never a finished resource)."""
    root = os.path.join(project_folder, MODELS)
    if os.path.isdir(root):
        for n in os.listdir(root):
            if n.startswith(".incoming-"):
                shutil.rmtree(os.path.join(root, n), True)


def _intact(folder: str, name: str, main_hash: str, sidecars: list) -> bool:
    """An earlier copy of this file (same name and bytes) with its sidecars all there."""
    p = os.path.join(folder, name)
    return os.path.isfile(p) and all(os.path.isfile(os.path.join(folder, s)) for s in sidecars) and sha256(p) == main_hash


def _inside(path: str, root: str) -> bool:
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r + os.sep)
