"""Built-in delivery presets. Users can duplicate and edit these in the app.

Any key left out (or null) is not checked. Lists mean "any of".
"""

BUILTIN_PRESETS = [
    {
        "id": "none",
        "name": "Technical checks only (no delivery spec)",
        "description": "Runs integrity, content and sequence checks without comparing to a spec.",
    },
    {
        "id": "lhs_hd_prores_25",
        "name": "HD ProRes 422 HQ · 25p · Stereo EBU R128",
        "description": "Typical UK broadcast / master delivery. 1920×1080 25p progressive, ProRes 422 HQ in MOV, "
                       "Rec.709, 24-bit 48 kHz stereo PCM at −23 LUFS ±1, true peak ≤ −1 dBTP.",
        "video": {
            "containers": ["mov"], "codecs": ["prores"], "profiles": ["HQ", "4444", "4444 XQ"],
            "width": 1920, "height": 1080, "fps": ["25/1"], "progressive": True,
            "bit_depth_min": 10, "chroma": ["4:2:2", "4:4:4"], "alpha": "any",
            "color_primaries": ["bt709"], "color_trc": ["bt709"], "color_space": ["bt709"],
            "par": "1:1",
        },
        "audio": {
            "required": True, "codecs": ["pcm_s24le", "pcm_s24be"], "sample_rate": 48000,
            "channels": 2, "loudness_target": -23.0, "loudness_tolerance": 1.0, "true_peak_max": -1.0,
        },
    },
    {
        "id": "uhd_prores4444_25",
        "name": "UHD ProRes 4444 · 25p · Alpha allowed (live / LED content)",
        "description": "3840×2160 25p ProRes 4444 for playback servers. Alpha permitted. Audio optional.",
        "video": {
            "containers": ["mov"], "codecs": ["prores"], "profiles": ["4444", "4444 XQ"],
            "width": 3840, "height": 2160, "fps": ["25/1"], "progressive": True,
            "bit_depth_min": 10, "chroma": ["4:4:4"], "alpha": "any",
            "color_primaries": ["bt709"], "par": "1:1",
        },
        "audio": {"required": False, "sample_rate": 48000},
    },
    {
        "id": "web_h264_1080",
        "name": "Web H.264 1080p · AAC · −14 LUFS",
        "description": "Online / social landscape master. 1920×1080, H.264 High in MP4, 8-bit 4:2:0 Rec.709, "
                       "AAC 48 kHz stereo at −14 LUFS ±1, true peak ≤ −1 dBTP.",
        "video": {
            "containers": ["mov", "mp4"], "codecs": ["h264"], "width": 1920, "height": 1080,
            "fps": ["24000/1001", "24/1", "25/1", "30000/1001", "30/1"], "progressive": True,
            "chroma": ["4:2:0"], "alpha": "forbidden", "color_primaries": ["bt709"], "par": "1:1",
        },
        "audio": {
            "required": True, "codecs": ["aac"], "sample_rate": 48000, "channels": 2,
            "loudness_target": -14.0, "loudness_tolerance": 1.0, "true_peak_max": -1.0,
        },
    },
    {
        "id": "social_vertical",
        "name": "Social vertical 1080×1920 · H.264 · 30p",
        "description": "9:16 portrait deliverable for stories / reels.",
        "video": {
            "containers": ["mov", "mp4"], "codecs": ["h264", "hevc"], "width": 1080, "height": 1920,
            "fps": ["30/1", "30000/1001", "25/1"], "chroma": ["4:2:0"], "alpha": "forbidden", "par": "1:1",
        },
        "audio": {"required": True, "codecs": ["aac"], "sample_rate": 48000, "channels": 2,
                  "loudness_target": -14.0, "loudness_tolerance": 1.5, "true_peak_max": -1.0},
    },
    {
        "id": "vfx_exr_1001",
        "name": "VFX EXR sequence · 16-bit half · start 1001",
        "description": "Plate / comp return. OpenEXR, half or float, frames start at 1001, 4-digit padding, "
                       "name.####.exr. Resolution taken from the first frame unless set.",
        "sequence": {
            "formats": ["exr"], "bit_depth": [16, 32], "start_frame": 1001, "padding": 4,
            "naming_regex": r"^[A-Za-z0-9_\-]+\.$", "alpha": "any",
        },
    },
]


def builtin_by_id():
    return {p["id"]: p for p in BUILTIN_PRESETS}
