"""Personal defaults for new projects: the reviewer's name, the preferred delivery spec and the
reusable analysis and IINA choices, remembered only when the user asks (Settings → Use these for
new projects) and copied into a project once, when it is created. No Qt: the app keeps the
remembered record in its own preferences (nitpix/ui/prefs.py, separate for experimental builds);
this module captures it from a project and seeds a new project from it.

- Project data stays in its project: the project name, client, sign-off, decisions, notes and
  media are never part of the defaults, and opening a project never changes them or the defaults.
- After seeding, the project's own settings win. Changing the defaults later changes no existing
  project, and never the author recorded on earlier notes, findings or sign-offs.
- A custom spec is remembered by its complete definition and written into the new project's own
  spec table, so the new project's copy can be edited without touching anything else. A spec
  that cannot be used (an unknown built-in, an invalid definition) is reported, and the project
  keeps the factory default spec rather than a silently different one.
"""
from __future__ import annotations

import copy
import json
import time

from . import vocab
from .presets import builtin_by_id
from .store import DEFAULT_SETTINGS, Project

VERSION = 1
SECTIONS = ("analysis", "iina")                  # the reusable groups of settings, as stored in a project


class DefaultsError(Exception):
    pass


def capture(p: Project) -> dict:
    """The personal defaults as this project now has them (its saved settings, not the factory's)."""
    s = p.settings
    pid = s.get("preset_id") or DEFAULT_SETTINGS["preset_id"]
    if pid in builtin_by_id():
        spec = {"id": pid, "builtin": True, "name": builtin_by_id()[pid].get("name", pid)}
    else:
        row = p.q1("SELECT id, name, spec FROM presets WHERE id=?", (pid,))
        if row is None:
            raise DefaultsError(f"the project's spec {pid!r} has no saved definition")
        spec = {"id": row["id"], "builtin": False, "name": row["name"], "spec": json.loads(row["spec"])}
    return {"version": VERSION, "saved": time.time(), "reviewer": s.get("reviewer") or "", "preset": spec,
            **{k: copy.deepcopy(s[k]) for k in SECTIONS}}


def check(d) -> dict:
    """A remembered record, checked; DefaultsError says what is wrong with it."""
    if not isinstance(d, dict) or d.get("version") != VERSION:
        raise DefaultsError("the remembered defaults are from another version of nitpix")
    pr = d.get("preset")
    if not isinstance(pr, dict) or not isinstance(pr.get("id"), str) or not isinstance(d.get("reviewer", ""), str):
        raise DefaultsError("the remembered defaults are damaged")
    for k in SECTIONS:
        if not isinstance(d.get(k, {}), dict):
            raise DefaultsError("the remembered defaults are damaged")
    return d


def seed(p: Project, d: dict) -> list[str]:
    """Copy the defaults into a project that has just been created. Returns what could not be used,
    for the user to see; everything else is applied."""
    problems = []
    try:
        d = check(d)
    except DefaultsError as e:
        return [f"Your defaults for new projects were not used: {e}."]
    patch = {"reviewer": d.get("reviewer", ""), **{k: copy.deepcopy(d.get(k, {})) for k in SECTIONS}}
    pr = d["preset"]
    if pr.get("builtin"):
        if pr["id"] in builtin_by_id():
            patch["preset_id"] = pr["id"]
        else:
            problems.append(f"Your default spec \"{pr.get('name', pr['id'])}\" is not in this version of nitpix, so this "
                            f"project starts with the factory default spec. Choose a spec in Settings.")
    else:
        try:
            if not isinstance(pr.get("spec"), dict) or not pr["spec"]:
                raise ValueError("its definition is missing")
            spec, _warnings = vocab.clean_preset(pr["spec"])
            p.x("INSERT INTO presets(id,name,spec,builtin,updated) VALUES(?,?,?,0,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, spec=excluded.spec, updated=excluded.updated",
                (pr["id"], pr.get("name") or pr["id"], json.dumps(spec), time.time()))
            patch["preset_id"] = pr["id"]
        except (ValueError, TypeError) as e:
            problems.append(f"Your default spec \"{pr.get('name', pr['id'])}\" could not be used ({e}), so this project "
                            f"starts with the factory default spec. Choose a spec in Settings.")
    p.update_settings(patch)
    return problems


def describe(d: dict | None) -> str:
    """One line for Settings: what new projects start with."""
    if not d:
        return "New projects start with nitpix's factory settings."
    try:
        d = check(d)
    except DefaultsError as e:
        return f"New projects start with nitpix's factory settings ({e})."
    who = d.get("reviewer") or "no reviewer name"
    return f"New projects start with: {who} · spec \"{d['preset'].get('name', d['preset']['id'])}\" · these analysis and IINA choices."
