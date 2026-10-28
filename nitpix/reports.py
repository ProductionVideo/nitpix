"""CSV / JSON / PDF batch reports. Written only into <project>/exports/."""
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime

from .probe import tool_versions
from .timecode import TimecodeContext, parse_rate

FONT_DIR = os.path.join(os.path.dirname(__file__), "web", "fonts")
REVIEW_LABEL = {"unreviewed": "Unreviewed", "in_review": "In review", "approved": "Approved",
                "approved_notes": "Approved with notes", "rejected": "Rejected"}
DECISION_LABEL = {None: "Unreviewed", "pass": "Pass", "issue": "Issue", "warning": "Warning", "na": "N/A"}


def position_label(asset: dict, frame):
    if frame is None:
        return ""
    if asset["kind"] == "sequence" and asset.get("seq"):
        return f"fr {asset['seq']['first'] + frame}"
    rate = parse_rate(asset.get("fps")) or (parse_rate(25) if asset["kind"] == "audio" else None)
    if not rate:
        return f"fr {frame}"
    return TimecodeContext(rate, asset.get("start_tc")).tc(frame)


def collect(project, asset_ids=None) -> dict:
    """The report's data. asset_ids None: every asset. A list is an explicit scope; an empty one is refused,
    never widened to the whole project."""
    s = project.settings
    if asset_ids is not None and not asset_ids:
        raise ValueError("No assets in this scope, so there is nothing to export.")
    if asset_ids is not None:
        qmarks = ",".join("?" * len(asset_ids))
        rows = project.q(f"SELECT id FROM assets WHERE id IN ({qmarks}) ORDER BY dir, name", asset_ids)
    else:
        rows = project.q("SELECT id FROM assets ORDER BY dir, name")
    assets = []
    for r in rows:
        a = project.asset(r["id"])
        a["findings"] = project.q("SELECT * FROM findings WHERE asset_id=? ORDER BY CASE category WHEN 'integrity' THEN 0 WHEN 'format' THEN 1 WHEN 'colour' THEN 2 WHEN 'audio' THEN 3 WHEN 'content' THEN 4 WHEN 'sequence' THEN 5 ELSE 6 END, id", (a["id"],))
        a["notes"] = project.q("SELECT * FROM notes WHERE asset_id=? ORDER BY frame, id", (a["id"],))
        assets.append(a)
    from .scanner import active_preset
    return {"settings": s, "preset": active_preset(project), "assets": assets, "generated": time.time(),
            "tools": tool_versions()}


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _slug(s):
    import re
    return re.sub(r"[^A-Za-z0-9\-]+", "_", s or "report").strip("_")[:60] or "report"


def export_csv(project, asset_ids=None) -> str:
    d = collect(project, asset_ids)
    name = f"{_slug(d['settings']['project_name'])}_QC_{_stamp()}.csv"
    out = project.safe_path("exports", name)
    cols = ["asset_id", "asset_name", "path", "type", "container", "video_codec", "resolution", "fps",
            "duration_s", "start_tc", "audio", "qc_result", "review_status", "signed_by", "signed_at",
            "row_type", "check", "category", "evidence", "auto_result", "confidence", "title", "measured",
            "expected", "position_in", "position_out", "frame_in", "frame_out", "reviewer_decision",
            "reviewer_note", "reviewed_by", "why_it_matters"]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for a in d["assets"]:
            base = [a["id"], a["name"], a["path"], a["kind"], a.get("container"), a.get("vcodec"),
                    f"{a['width']}x{a['height']}" if a.get("width") else "", a.get("fps_float") or "",
                    round(a["duration"], 3) if a.get("duration") else "", a.get("start_tc") or "",
                    f"{a.get('audio_channels') or 0}ch {a.get('sample_rate') or ''}".strip(),
                    a.get("qc_result"), a.get("review_status"), a.get("signed_by") or "",
                    datetime.fromtimestamp(a["signed_at"]).isoformat(timespec="seconds") if a.get("signed_at") else ""]
            for fd in a["findings"]:
                w.writerow(base + ["finding", fd["check_key"], fd["category"], fd["kind"], fd["auto_result"],
                                   fd["confidence"], fd["title"], fd["measured"] or "", fd["expected"] or "",
                                   position_label(a, fd["frame_in"]), position_label(a, fd["frame_out"]),
                                   "" if fd["frame_in"] is None else fd["frame_in"],
                                   "" if fd["frame_out"] is None else fd["frame_out"],
                                   fd["reviewer_status"] or "", fd["reviewer_note"] or "", fd["reviewed_by"] or "",
                                   fd["why"]])
            for n in a["notes"]:
                w.writerow(base + [n["kind"], "reviewer." + n["kind"], "observation", "observed", n["severity"] or "",
                                   "", n["text"], "", "", position_label(a, n["frame"]),
                                   position_label(a, n["frame_out"]), n["frame"] if n["frame"] is not None else "",
                                   n["frame_out"] if n["frame_out"] is not None else "", "", "", n["author"] or "", ""])
    return str(out)


def export_json(project, asset_ids=None) -> str:
    d = collect(project, asset_ids)
    name = f"{_slug(d['settings']['project_name'])}_QC_{_stamp()}.json"
    out = project.safe_path("exports", name)
    doc = {
        "schema": "nitpix-review/1",
        "generated": datetime.fromtimestamp(d["generated"]).isoformat(timespec="seconds"),
        "project": {k: d["settings"].get(k) for k in ("project_name", "client", "reviewer")},
        "preset": d["preset"],
        "tools": d["tools"],
        "assets": [],
    }
    for a in d["assets"]:
        seq = a.get("seq")
        if seq:
            seq = {k: v for k, v in seq.items() if k != "frames"}
        meta = dict(a.get("metadata") or {})
        if "analysis" in meta:
            meta["analysis"] = {k: v for k, v in meta["analysis"].items() if k not in ("yavg", "audio_rms")}
        doc["assets"].append({
            **{k: a.get(k) for k in ("id", "kind", "name", "path", "size", "container", "vcodec", "acodec", "width",
                                     "height", "fps", "duration", "frames", "start_tc", "qc_result",
                                     "review_status", "signed_by", "signed_at", "in_frame", "out_frame", "scan_error")},
            "metadata": meta, "sequence": seq,
            "findings": [{**f, "position_in": position_label(a, f["frame_in"]),
                          "position_out": position_label(a, f["frame_out"])} for f in a["findings"]],
            "notes": [{**n, "position": position_label(a, n["frame"])} for n in a["notes"]],
        })
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, default=str)
    return str(out)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    try:
        pdfmetrics.registerFont(TTFont("IT", os.path.join(FONT_DIR, "InterTight_400Regular.ttf")))
        pdfmetrics.registerFont(TTFont("IT-M", os.path.join(FONT_DIR, "InterTight_500Medium.ttf")))
        pdfmetrics.registerFont(TTFont("IT-B", os.path.join(FONT_DIR, "InterTight_600SemiBold.ttf")))
        pdfmetrics.registerFont(TTFont("PM", os.path.join(FONT_DIR, "IBMPlexMono_400Regular.ttf")))
        return "IT", "IT-M", "IT-B", "PM"
    except Exception:
        return "Helvetica", "Helvetica", "Helvetica-Bold", "Courier"


def export_pdf(project, asset_ids=None) -> str:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    from xml.sax.saxutils import escape as esc

    d = collect(project, asset_ids)
    S = d["settings"]
    name = f"{_slug(S['project_name'])}_QC_{_stamp()}.pdf"
    out = project.safe_path("exports", name)
    R, M, B, MONO = _fonts()
    INK = colors.HexColor("#0E0E0E")
    MUTED = colors.HexColor("#6B6B6B")
    LINE = colors.HexColor("#E2E2E2")
    COL = {"pass": colors.HexColor("#1F7A4D"), "warning": colors.HexColor("#B26A00"),
           "fail": colors.HexColor("#C2261B"), "issue": colors.HexColor("#C2261B"),
           "info": colors.HexColor("#6B6B6B"), "na": colors.HexColor("#6B6B6B"), None: colors.HexColor("#6B6B6B")}

    st = {
        "h1": ParagraphStyle("h1", fontName=B, fontSize=22, leading=26, textColor=INK),
        "h2": ParagraphStyle("h2", fontName=B, fontSize=13, leading=16, textColor=INK, spaceBefore=4, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontName=M, fontSize=9.5, leading=12, textColor=INK, spaceBefore=8, spaceAfter=3),
        "p": ParagraphStyle("p", fontName=R, fontSize=8.5, leading=11, textColor=INK),
        "muted": ParagraphStyle("m", fontName=R, fontSize=8, leading=10.5, textColor=MUTED),
        "mono": ParagraphStyle("mono", fontName=MONO, fontSize=7.5, leading=9.5, textColor=INK),
        "cell": ParagraphStyle("c", fontName=R, fontSize=7.6, leading=9.4, textColor=INK, alignment=TA_LEFT),
        "cellm": ParagraphStyle("cm", fontName=MONO, fontSize=7.2, leading=9, textColor=INK),
        "head": ParagraphStyle("hd", fontName=M, fontSize=7.2, leading=9, textColor=MUTED),
    }

    def P(t, s="cell"):
        return Paragraph(esc(str(t if t is not None else "")).replace("\n", "<br/>"), st[s])

    def chip(res, label=None):
        c = COL.get(res, MUTED)
        return Paragraph(f'<font color="{c.hexval()}"><b>●</b></font> {esc(label or (res or "—").capitalize())}', st["cell"])

    def tbl(rows, widths, head=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if head else 0)
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK) if head else ("LINEBELOW", (0, 0), (-1, 0), 0, colors.white),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    W = landscape(A4)[0] - 30 * mm
    doc = SimpleDocTemplate(str(out), pagesize=landscape(A4), leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=16 * mm, bottomMargin=14 * mm, title=f"QC report — {S['project_name']}",
                            author=S.get("reviewer") or "nitpix")
    gen = datetime.fromtimestamp(d["generated"]).strftime("%d %b %Y %H:%M")

    def on_page(c, docx):
        c.saveState()
        c.setFont(MONO, 7)
        c.setFillColor(MUTED)
        c.drawString(15 * mm, 8 * mm, f"nitpix · {S['project_name']} · generated {gen} · source media read-only, not modified")
        c.drawRightString(landscape(A4)[0] - 15 * mm, 8 * mm, f"Page {docx.page}")
        c.setStrokeColor(INK)
        c.setLineWidth(1.2)
        c.line(15 * mm, landscape(A4)[1] - 10 * mm, 22 * mm, landscape(A4)[1] - 10 * mm)
        c.restoreState()

    assets = d["assets"]
    totals = {"pass": 0, "warning": 0, "fail": 0}
    for a in assets:
        totals[a.get("qc_result") or "warning"] = totals.get(a.get("qc_result") or "warning", 0) + 1
    rev = {}
    for a in assets:
        rev[a.get("review_status") or "unreviewed"] = rev.get(a.get("review_status") or "unreviewed", 0) + 1
    open_detect = sum(1 for a in assets for f in a["findings"]
                      if f["kind"] == "detected" and f["auto_result"] in ("warning", "fail") and not f["reviewer_status"])

    story = [Paragraph("QC report", st["h1"]), Spacer(1, 2),
             Paragraph(esc(S["project_name"]) + (f" · {esc(S['client'])}" if S.get("client") else ""), st["h2"]),
             Spacer(1, 6)]
    info = [[P("Delivery spec", "head"), P(d["preset"].get("name")), P("Generated", "head"), P(gen)],
            [P("Reviewer", "head"), P(S.get("reviewer") or "—"), P("Assets", "head"), P(len(assets))],
            [P("Spec detail", "head"), P(d["preset"].get("description") or "—"), P("Tools", "head"),
             P(" / ".join((v.get("version") or "missing").split(" Copyright")[0] for v in d["tools"].values()), "cellm")]]
    story.append(tbl(info, [32 * mm, W / 2 - 32 * mm, 26 * mm, W / 2 - 26 * mm], head=False))
    story.append(Spacer(1, 10))
    kpi = [[P("Automated result", "head"), "", "", P("Review status", "head"), "", "", "", P("Detections awaiting review", "head")],
           [chip("pass", f"{totals.get('pass', 0)} pass"), chip("warning", f"{totals.get('warning', 0)} warning"),
            chip("fail", f"{totals.get('fail', 0)} fail"),
            chip("pass", f"{rev.get('approved', 0) + rev.get('approved_notes', 0)} approved"),
            chip("fail", f"{rev.get('rejected', 0)} rejected"), chip("info", f"{rev.get('in_review', 0)} in review"),
            chip("info", f"{rev.get('unreviewed', 0)} unreviewed"),
            chip("warning" if open_detect else "pass", f"{open_detect} open")]]
    story.append(tbl(kpi, [W / 8] * 8, head=False))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Results are split into <b>measured</b> facts read from the file, <b>automated detections</b> (heuristics such as "
        "black, freeze, flash, silence and clipping, which may be wrong and need a reviewer's decision) and reviewer "
        "<b>observations</b>. An asset's result reflects reviewer decisions where they exist.", st["muted"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Batch summary", st["h2"]))
    rows = [[P(h, "head") for h in ("Asset", "Type", "Format", "Resolution", "FPS", "Duration", "Start TC",
                                     "Result", "Review", "Signed off")]]
    for a in assets:
        dur = f"{a['duration']:.2f}s" if a.get("duration") else "—"
        rows.append([P(a["name"]), P(a["kind"]), P(" ".join(x for x in (a.get("container"), a.get("vcodec")) if x) or "—"),
                     P(f"{a['width']}×{a['height']}" if a.get("width") else "—"),
                     P(f"{a['fps_float']:g}" if a.get("fps_float") else "—"), P(dur), P(a.get("start_tc") or "—", "cellm"),
                     chip(a.get("qc_result")), P(REVIEW_LABEL.get(a.get("review_status"), a.get("review_status"))),
                     P((a.get("signed_by") or "") + (f"\n{datetime.fromtimestamp(a['signed_at']).strftime('%d %b %H:%M')}" if a.get("signed_at") else ""))])
    story.append(tbl(rows, [62 * mm, 16 * mm, 30 * mm, 22 * mm, 12 * mm, 16 * mm, 24 * mm, 20 * mm, 26 * mm, W - 228 * mm]))

    for a in assets:
        story.append(PageBreak())
        story.append(Paragraph(esc(a["name"]), st["h2"]))
        story.append(Paragraph(esc(a["path"]), st["mono"]))
        story.append(Spacer(1, 4))
        status = [[chip(a.get("qc_result"), f"Automated: {a.get('qc_result') or '—'}"),
                   P(f"Review: {REVIEW_LABEL.get(a.get('review_status'), a.get('review_status'))}"),
                   P(f"Signed off: {a.get('signed_by') or '—'}"),
                   P(f"In/Out: {position_label(a, a.get('in_frame')) or '—'} – {position_label(a, a.get('out_frame')) or '—'}")]]
        story.append(tbl(status, [W / 4] * 4, head=False))
        if a.get("scan_error"):
            story.append(Paragraph(f"<b>Error:</b> {esc(a['scan_error'])}", st["p"]))
        meas = [f for f in a["findings"] if f["kind"] == "measured"]
        det = [f for f in a["findings"] if f["kind"] == "detected"]
        if meas:
            story.append(Paragraph("Measured results", st["h3"]))
            rows = [[P(h, "head") for h in ("Check", "Result", "Measured", "Expected", "Reviewer", "Why it matters")]]
            for f in meas:
                rv = f["reviewer_status"]
                rows.append([P(f["title"] + (f"\n{f['detail']}" if f["detail"] and f["auto_result"] != "pass" else "")),
                             chip(f["auto_result"]), P(f["measured"] or "", "cellm"), P(f["expected"] or "", "cellm"),
                             P(("—" if rv is None else DECISION_LABEL.get(rv, rv)) + (f"\n{f['reviewer_note']}" if f["reviewer_note"] else "")),
                             P(f["why"], "muted")])
            story.append(tbl(rows, [58 * mm, 18 * mm, 52 * mm, 40 * mm, 34 * mm, W - 202 * mm]))
        if det:
            story.append(Paragraph("Automated detections — confirm or dismiss", st["h3"]))
            rows = [[P(h, "head") for h in ("Detection", "Position", "Confidence", "Auto", "Reviewer decision", "Why it matters")]]
            for f in det:
                pos = position_label(a, f["frame_in"])
                if f["frame_out"] is not None and f["frame_out"] != f["frame_in"]:
                    pos += f" – {position_label(a, f['frame_out'])}"
                rv = f["reviewer_status"]
                rows.append([P(f["title"] + (f"\n{f['detail']}" if f["detail"] else "")), P(pos, "cellm"),
                             P(f["confidence"]), chip(f["auto_result"]),
                             (P("—") if rv is None and f["auto_result"] in ("pass", "info") else chip(rv, DECISION_LABEL.get(rv, rv))),
                             P(f["why"], "muted")])
                if f["reviewer_note"]:
                    rows[-1][4] = Paragraph(f'{esc(DECISION_LABEL.get(rv, rv))}<br/><font color="#6B6B6B">{esc(f["reviewer_note"])}</font>', st["cell"])
            story.append(tbl(rows, [70 * mm, 40 * mm, 18 * mm, 18 * mm, 40 * mm, W - 186 * mm]))
        if a["notes"]:
            story.append(Paragraph("Reviewer observations", st["h3"]))
            rows = [[P(h, "head") for h in ("Type", "Position", "Severity", "Observation", "By")]]
            for n in a["notes"]:
                pos = position_label(a, n["frame"])
                if n["frame_out"] is not None and n["frame_out"] != n["frame"]:
                    pos += f" – {position_label(a, n['frame_out'])}"
                rows.append([P(n["kind"].capitalize()), P(pos, "cellm"),
                             chip({"issue": "fail"}.get(n["severity"], n["severity"]), n["severity"] or "info"),
                             P(n["text"]), P(n["author"] or "")])
            story.append(tbl(rows, [22 * mm, 40 * mm, 22 * mm, W - 124 * mm, 40 * mm]))
        seq = a.get("seq")
        if seq:
            story.append(Paragraph("Sequence", st["h3"]))
            gaps = ", ".join(f"{x}" if x == y else f"{x}–{y}" for x, y in seq["missing"][:40]) or "none"
            story.append(tbl([[P("Pattern", "head"), P(seq["pattern"], "cellm"), P("Range", "head"),
                               P(f"{seq['first']}–{seq['last']} ({seq['count']}/{seq['expected']} present)")],
                              [P("Missing", "head"), P(gaps, "cellm"), P("Size", "head"),
                               P(f"{seq['total_size'] / 1e6:.1f} MB")]],
                             [22 * mm, W / 2 - 22 * mm, 22 * mm, W / 2 - 22 * mm], head=False))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return str(out)
