"""Playable targets for assets: a file path, or an mf:// frame list for image sequences."""
from __future__ import annotations

import os


def sequence_files(seq: dict) -> list[str]:
    """One path per frame index from first to last. Gaps repeat the previous frame so
    frame n of the playlist is always frame number first + n of the sequence."""
    frames = {r[0]: r[1] for r in seq["frames"]}
    out, last = [], None
    for n in range(seq["first"], seq["last"] + 1):
        name = frames.get(n) or last
        if name is None:
            continue
        last = name
        out.append(os.path.join(seq["dir"], name))
    return out


def target(project, asset: dict) -> str:
    """What to hand mpv for this asset."""
    if asset["kind"] != "sequence":
        return asset["path"]
    lst = project.safe_path("cache", f"seq_{asset['id']}.txt")
    lst.write_text("\n".join(sequence_files(asset["seq"])) + "\n", encoding="utf-8")
    return f"mf://@{lst}"
