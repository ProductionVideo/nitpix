#!/usr/bin/env python3
"""A small generated demo for the projector setup workflow: a project with one stage model, one
clip and a Mapping setup saved in it, ready to open in nitpix and try.

    .venv/bin/python tools/make_setup_demo.py <folder>        # writes <folder>/projector-demo.nitpix

Needs ffmpeg/ffprobe (as nitpix does). Everything is made here; nothing outside <folder> is read
or written.

The stage (metres, Y up, the audience at +Z; no UVs anywhere):
  Screen    4 x 2.25 m at z = 0, x -2..2, y 0.5..2.75 (centre 0, 1.625, 0), facing +Z
  Occluder  a 0.5 m box centred (1.1, 1.3, 2.4): in the beam's right half, nearer the projector
  Floor     10 x 10 m at y = 0, x -5..5, z -2..8, facing up
The clip: 1920 x 1080, 25 fps, 12 s; TOP / BOTTOM / LEFT / RIGHT at its edges, a moving test
pattern and a frame counter, so orientation and motion read when projected.

The saved setup starts in the Projectors tab with the projector off to the side and aimed at the
floor (1.0 : 1). Moving it to x = 0, z = 6, picking the screen and setting 1.5 : 1 gives the
4 x 2.25 m image that fills the screen (6 m / 1.5 = 4 m wide; 1920 x 1080 makes it 2.25 m high).
"""
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nitpix import mapsave, models, service  # noqa: E402
from nitpix.projector import Projector  # noqa: E402

START = Projector(position=(3.0, 1.625, 7.5), target=(3.0, 0.4, 3.5), throw=1.0, raster=(1920, 1080))


def box(name, c, h):
    x0, y0, z0 = (c[i] - h for i in range(3))
    x1, y1, z1 = (c[i] + h for i in range(3))
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(4, 5, 6, 7), (1, 0, 3, 2), (0, 4, 7, 3), (5, 1, 2, 6), (7, 6, 2, 3), (0, 1, 5, 4)]   # outward, counter-clockwise
    return name, v, faces


def stage_obj(path):
    objects = [("Screen", [(-2, 0.5, 0), (2, 0.5, 0), (2, 2.75, 0), (-2, 2.75, 0)], [(0, 1, 2, 3)]),
               box("Occluder", (1.1, 1.3, 2.4), 0.25),
               ("Floor", [(-5, 0, 8), (5, 0, 8), (5, 0, -2), (-5, 0, -2)], [(0, 1, 2, 3)])]
    lines, base = ["# nitpix projector setup demo (tools/make_setup_demo.py); metres, Y up; no UVs"], 1
    for name, verts, faces in objects:
        lines.append(f"o {name}")
        lines += [f"v {x:.6g} {y:.6g} {z:.6g}" for x, y, z in verts]
        lines += ["f " + " ".join(str(base + i) for i in f) for f in faces]
        base += len(verts)
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def clip(path):
    text = "fontcolor=white:fontsize={s}:box=1:boxcolor=black@0.6:boxborderw=14:text='{t}':x={x}:y={y}"
    labels = [("TOP", "(w-text_w)/2", "28", 72), ("BOTTOM", "(w-text_w)/2", "h-text_h-28", 72),
              ("LEFT", "28", "(h-text_h)/2", 72), ("RIGHT", "w-text_w-28", "(h-text_h)/2", 72),
              ("nitpix projector demo", "(w-text_w)/2", "h*0.30", 54)]
    vf = ",".join("drawtext=" + text.format(s=s, t=t, x=x, y=y) for t, x, y, s in labels)
    vf += (",drawtext=fontcolor=yellow:fontsize=64:box=1:boxcolor=black@0.6:boxborderw=12:"
           "text='frame %{frame_num}':x=(w-text_w)/2:y=h*0.62,"
           "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=10")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1920x1080:rate=25:duration=12", "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "23", "-preset", "veryfast", "-movflags", "+faststart", path], check=True)


def main(out):
    out = os.path.abspath(out)
    folder = os.path.join(out, "projector-demo.nitpix")
    src = os.path.join(out, "demo source")
    for p in (folder, src):
        shutil.rmtree(p, ignore_errors=True)
    os.makedirs(src)
    model = os.path.join(src, "projector-demo.obj")
    stage_obj(model)
    media = os.path.join(src, "orientation.mp4")
    clip(media)
    s = service.Session()
    s.open(folder, "Projector setup demo")
    p = s.project
    job = s.jobs.scan_roots([media])
    while s.jobs.active:
        time.sleep(0.2)
    aid = p.q1("SELECT id FROM assets")["id"]
    res = models.manage(model, str(p.folder))
    rid = res.pop("id")
    res.pop("created")
    doc = {"version": mapsave.VERSION, "resources": {rid: res}, "sources": [{"id": "m1", "resource": rid, "origin": model}],
           "next_id": 2, "assignments": {}, "clip": aid, "tab": "projectors",
           "camera": {"yaw": 28.0, "pitch": 16.0, "dist": 13.5, "target": [0.6, 1.2, 2.6], "view": "perspective"},
           "projectors": [mapsave.projector_to(START)]}
    mapsave.validate(json.loads(json.dumps(doc)))
    p.set_meta(mapsave.KEY, doc)
    s.close()
    print(folder, f"(scan {job.state if hasattr(job, 'state') else 'done'})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
