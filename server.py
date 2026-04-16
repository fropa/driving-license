#!/usr/bin/env python3
"""grU Trainer — Flask backend with per-user progress persistence."""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

app = Flask(__name__)

BASE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data"))
STATIC = BASE / "static"
APP_BASE = os.environ.get("APP_BASE", "/").rstrip("/") + "/"  # e.g. "/driving/" or "/"
DATA.mkdir(parents=True, exist_ok=True)


# ── helpers ─────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^\w-]", "_", name.strip().lower())[:40]

def _path(name: str) -> Path:
    return DATA / f"{_slug(name)}.json"

def _now() -> str:
    return datetime.now().isoformat()

def _load(name: str) -> dict:
    p = _path(name)
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    return {"name": name, "created": _now(), "progress": {}, "sessions": []}

def _save(name: str, d: dict):
    _path(name).write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")

def _compute_stats(d: dict) -> dict:
    prog    = d.get("progress", {})
    now_ms  = datetime.now().timestamp() * 1000
    DAY_MS  = 86_400_000

    seen       = sum(1 for v in prog.values() if v.get("attempts", 0) > 0)
    mastered   = sum(1 for v in prog.values() if v.get("streak", 0) >= 3)
    struggling = sum(1 for v in prog.values()
                     if v.get("attempts", 0) >= 2
                     and v.get("correct", 0) / v["attempts"] < 0.6)
    due        = sum(1 for v in prog.values()
                     if v.get("attempts", 0) > 0
                     and v.get("lastSeen", 0) + v.get("interval", 0) * DAY_MS <= now_ms)

    sessions = d.get("sessions", [])

    # per-chapter error rates
    chapter_errors: dict[int, dict] = {}
    for tid, v in prog.items():
        if v.get("attempts", 0) < 2:
            continue
        cid = v.get("chapter_id")
        if cid is None:
            continue
        bucket = chapter_errors.setdefault(cid, {"wrong": 0, "total": 0})
        bucket["total"] += v["attempts"]
        bucket["wrong"] += v["attempts"] - v.get("correct", 0)

    weak_chapters = sorted(
        [
            {"chapter_id": cid, "error_rate": round(b["wrong"] / b["total"], 3)}
            for cid, b in chapter_errors.items()
            if b["total"] >= 3 and b["wrong"] / b["total"] > 0.15
        ],
        key=lambda x: -x["error_rate"],
    )[:8]

    return {
        "seen":          seen,
        "mastered":      mastered,
        "struggling":    struggling,
        "due":           due,
        "total_sessions": len(sessions),
        "last_session":  sessions[-1] if sessions else None,
        "weak_chapters": weak_chapters,
    }


# ── static ──────────────────────────────────────────────────────────────────

@app.after_request
def no_cache(r):
    r.headers["Cache-Control"] = "no-store, no-cache"
    return r

@app.route("/")
def index():
    if APP_BASE == "/":
        return send_from_directory(BASE, "study.html")
    # Inject <base href> so relative paths resolve under the subpath
    html = (BASE / "study.html").read_text("utf-8")
    html = html.replace("<head>", f'<head><base href="{APP_BASE}">', 1)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}

@app.route("/tickets.json")
def tickets():
    return send_from_directory(BASE, "tickets.json")

@app.route("/static/img/<path:filename>")
def static_img(filename):
    return send_from_directory(STATIC / "img", filename)


# ── API ─────────────────────────────────────────────────────────────────────

@app.route("/api/users", methods=["GET"])
def list_users():
    out = []
    for f in sorted(DATA.glob("*.json")):
        try:
            d = json.loads(f.read_text("utf-8"))
            out.append({"name": d["name"], **_compute_stats(d)})
        except Exception:
            pass
    return jsonify(out)


@app.route("/api/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        abort(400, "name required")
    is_new = not _path(name).exists()
    d = _load(name)
    if is_new:
        _save(name, d)
    return jsonify({
        "name":     d["name"],
        "is_new":   is_new,
        "progress": d.get("progress", {}),
        "stats":    _compute_stats(d),
    })


@app.route("/api/progress", methods=["PUT"])
def update_progress():
    body = request.get_json(silent=True) or {}
    name    = (body.get("name") or "").strip()
    updates = body.get("updates", {})
    if not name or not isinstance(updates, dict):
        abort(400)
    d = _load(name)
    if body.get("_reset"):
        d["progress"] = {}
    else:
        d.setdefault("progress", {}).update(updates)
    _save(name, d)
    return jsonify({"ok": True})


@app.route("/api/session", methods=["POST"])
def save_session():
    body = request.get_json(silent=True) or {}
    name    = (body.get("name") or "").strip()
    summary = body.get("summary", {})
    if not name:
        abort(400)
    d = _load(name)
    summary["date"] = _now()
    d.setdefault("sessions", []).append(summary)
    d["sessions"] = d["sessions"][-100:]
    _save(name, d)
    return jsonify({"ok": True})


# ── run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"grU Trainer  →  http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
