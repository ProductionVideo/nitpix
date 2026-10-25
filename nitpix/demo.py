"""The demo delivery: a small set of media with deliberately planted problems.

Generated with ffmpeg into a folder outside the app (the bundle may be read-only), in
about 30–60 s and ~250 MB. `tools/make_demo.py` is the command-line wrapper."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .probe import find_tool

DELIVERY_NAME = "DEMO_DELIVERY_Spring_Campaign"
PROJECT_NAME = "Spring Campaign — demo delivery"


def locations() -> dict:
    base = Path.home() / "nitpix Projects"
    return {"delivery": base / "demo" / DELIVERY_NAME, "project": base / "demo" / "Demo_Spring_Campaign.nitpix"}


def generate(out: str | os.PathLike, log=print):
    out = os.path.abspath(out)
    FF = find_tool("ffmpeg")
    if not FF:
        raise RuntimeError("ffmpeg not found; the demo delivery cannot be generated.")

    def ff(*args):
        cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", *args]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}\n{r.stderr[-400:]}")

    def mk(*parts):
        p = os.path.join(out, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    HD = "testsrc2=size=1920x1080:rate=25"

    def tone(freq, dur):  # ~ -23 LUFS stereo programme: two detuned tones at moderate level
        return f"sine=frequency={freq}:sample_rate=48000:duration={dur},volume=-4.3dB"

    log(f"Writing demo delivery to {out}")
    os.makedirs(out, exist_ok=True)
    m = lambda n: mk("01_MASTERS", n)
    prores = ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
              "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-c:a", "pcm_s24le"]
    stereo = "[1:a][2:a]join=inputs=2:channel_layout=stereo[a]"

    # 1. Clean master — should pass the HD ProRes preset
    ff("-f", "lavfi", "-i", f"{HD}:duration=8", "-f", "lavfi", "-i", tone(440, 8), "-f", "lavfi", "-i", tone(554, 8),
       "-filter_complex", stereo, "-map", "0:v", "-map", "[a]", *prores, "-timecode", "10:00:00:00", m("DEMO_Hero_30s_HD_v03.mov"))
    # 2. Black hold 3.0-4.0 s and a single white flash frame at frame 150 (6.0 s)
    ff("-f", "lavfi", "-i", f"{HD}:duration=8", "-f", "lavfi", "-i", tone(440, 8), "-f", "lavfi", "-i", tone(554, 8),
       "-filter_complex", "[0:v]drawbox=c=black:t=fill:enable='between(t,3,3.999)',drawbox=c=white:t=fill:enable='eq(n,150)'[v];" + stereo,
       "-map", "[v]", "-map", "[a]", *prores, "-timecode", "10:01:00:00", m("DEMO_Hero_30s_HD_v03_ALT.mov"))
    # 3. Frozen picture 2.0-4.4 s (frames 50-110 repeat frame 50)
    ff("-f", "lavfi", "-i", f"{HD}:duration=7", "-f", "lavfi", "-i", tone(440, 7), "-f", "lavfi", "-i", tone(554, 7),
       "-filter_complex", "[0:v]split[f0][f1];[f0][f1]freezeframes=first=50:last=110:replace=50[v];" + stereo,
       "-map", "[v]", "-map", "[a]", *prores, "-timecode", "10:02:00:00", m("DEMO_Teaser_15s_HD_v01.mov"))
    # 4. Web encode delivered by mistake: H.264 23.976, AAC 44.1k, hot + clipped audio
    ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24000/1001:duration=6",
       "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=6",
       "-filter_complex", "[1:a]aformat=sample_fmts=s16:channel_layouts=stereo,volume=26dB:precision=fixed[a]",
       "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
       "-c:a", "aac", "-b:a", "192k", m("DEMO_Teaser_15s_WEB.mp4"))
    # 5. Right channel drops out from 4 s
    ff("-f", "lavfi", "-i", f"{HD}:duration=8", "-f", "lavfi", "-i", tone(440, 8), "-f", "lavfi", "-i", tone(554, 8),
       "-filter_complex", "[2:a]volume=enable='gte(t,4)':volume=0[r];[1:a][r]join=inputs=2:channel_layout=stereo[a]",
       "-map", "0:v", "-map", "[a]", *prores, "-timecode", "10:03:00:00", m("DEMO_Cutdown_06s_HD_v02.mov"))
    # 6. Vertical ProRes 4444 with alpha, no audio, untagged colour
    ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=25:duration=4",
       "-vf", "format=yuva444p10le,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='if(lt(Y,H/2),255*4,512)'",
       "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", m("DEMO_Social_9x16_ALPHA_v01.mov"))
    # 7. Anamorphic 1440x1080 (PAR 4:3), ProRes LT, mono 48k, no timecode
    ff("-f", "lavfi", "-i", "testsrc2=size=1440x1080:rate=25:duration=4", "-f", "lavfi", "-i", tone(440, 4),
       "-vf", "setsar=4/3", "-c:v", "prores_ks", "-profile:v", "1", "-pix_fmt", "yuv422p10le",
       "-c:a", "pcm_s16le", m("DEMO_Archive_Insert_HDV.mov"))
    # 8. Zero-byte and truncated masters
    open(m("DEMO_Hero_30s_HD_v03_TEXTLESS.mov"), "wb").close()
    with open(m("DEMO_Hero_30s_HD_v03.mov"), "rb") as f:
        data = f.read()
    with open(m("DEMO_Hero_30s_HD_v03_COPY.mov"), "wb") as f:
        f.write(data[: int(len(data) * 0.45)])

    # Audio
    a = lambda n: mk("02_AUDIO", n)
    ff("-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000:duration=10,volume=-2dB",
       "-af", "volume=enable='between(t,4,7)':volume=0,aformat=channel_layouts=stereo",
       "-c:a", "pcm_s24le", a("DEMO_Hero_MIX_Stereo_24b48k.wav"))
    srcs = []
    for fq in (300, 310, 320, 60, 330, 340):
        srcs += ["-f", "lavfi", "-i", f"sine=frequency={fq}:sample_rate=48000:duration=6,volume=-8dB"]
    ff(*srcs, "-filter_complex", "".join(f"[{i}:a]" for i in range(6)) + "join=inputs=6:channel_layout=5.1[a]",
       "-map", "[a]", "-c:a", "pcm_s24le", a("DEMO_Hero_MIX_51_24b48k.wav"))

    # VFX: PNG sequence with planted faults
    sdir = mk("03_VFX", "shotA_comp_v004", "x")[:-1]
    ff("-f", "lavfi", "-i", "testsrc2=size=960x540:rate=25:duration=2.88", "-start_number", "1001",
       os.path.join(sdir, "shotA_comp_v004.%04d.png"))
    for n in (1031, 1032, 1033):                       # missing frames
        os.remove(os.path.join(sdir, f"shotA_comp_v004.{n}.png"))
    shutil.copyfile(os.path.join(sdir, "shotA_comp_v004.1049.png"), os.path.join(sdir, "shotA_comp_v004.1050.png"))  # repeated frame
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=0.04", "-frames:v", "1",
       "-update", "1", os.path.join(sdir, "shotA_comp_v004.1060.png"))  # wrong resolution
    p = os.path.join(sdir, "shotA_comp_v004.1066.png")
    with open(p, "rb") as f:
        d = f.read()
    with open(p, "wb") as f:
        f.write(d[: len(d) // 3])                       # truncated render
    open(os.path.join(sdir, "shotA_comp_v004.1069.png"), "wb").close()   # zero-byte
    shutil.copyfile(os.path.join(sdir, "shotA_comp_v004.1040.png"), os.path.join(sdir, "shotA_comp_v004.01040.png"))  # mis-padded duplicate
    # VFX: clean EXR plate
    edir = mk("03_VFX", "shotB_plate_v002", "x")[:-1]
    ff("-f", "lavfi", "-i", "testsrc2=size=960x540:rate=25:duration=0.96", "-start_number", "1001",
       "-pix_fmt", "gbrpf32le", "-c:v", "exr", "-compression", "zip1", "-format", "half", os.path.join(edir, "shotB_plate_v002.%04d.exr"))
    # VFX: DPX sequence starting at 1 with underscore naming (fails VFX preset naming / start frame)
    ddir = mk("03_VFX", "shotC_bg", "x")[:-1]
    ff("-f", "lavfi", "-i", "testsrc2=size=960x540:rate=25:duration=0.8", "-start_number", "1",
       "-pix_fmt", "gbrp10le", os.path.join(ddir, "shotC_bg_%04d.dpx"))

    # Misc
    x = lambda n: mk("04_MISC", n)
    with open(x("delivery_notes.txt"), "w") as f:
        f.write("Spring campaign delivery. See masters folder.\n")
    open(x("placeholder_textless.txt"), "wb").close()
    ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080", "-frames:v", "1", "-pix_fmt", "rgb48le", x("DEMO_Poster_Still_v01.tif"))
    log("Done.")
