"""taildrop-dl — share a URL from your phone, get the media Taildropped back.

POST /grab {"url": ..., "mode": "video"|"audio", "target": <optional device>}
  (Authorization: Bearer API_TOKEN)
Downloads via yt-dlp, sends the file to a Tailscale device with `tailscale file cp`,
optionally pings an ntfy topic. Files are deleted after delivery.

By default nothing content-related (URLs, titles, error text) is ever logged or
kept after a job finishes — set LOG_CONTENT=true to trade that for debuggability.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
import yt_dlp
from flask import Flask, jsonify, request

API_TOKEN = os.environ["API_TOKEN"]
TARGETS = [t.strip() for t in os.environ["TAILDROP_TARGETS"].split(",") if t.strip()]
if not TARGETS:
    raise SystemExit("TAILDROP_TARGETS must list at least one Tailscale device name")
BIND = os.environ.get("BIND", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8094"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "2"))
LOG_CONTENT = os.environ.get("LOG_CONTENT", "false").lower() in ("1", "true", "yes")
TAILSCALE_SOCKET = os.environ.get("TAILSCALE_SOCKET", "/var/run/tailscale/tailscaled.sock")
NTFY_BASE = os.environ.get("NTFY_BASE", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "taildrop-dl")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "")
TAILSCALE = ["tailscale", f"--socket={TAILSCALE_SOCKET}"]

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
jobs = {}  # id -> {"state": ..., "url": ..., "mode": ..., "target": ..., "detail": ...}
jobs_lock = threading.Lock()


class _Silent:
    """Swallow all yt-dlp output — URLs/titles must never reach the container log."""

    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def notify(title, message, tags="inbox_tray"):
    if not (NTFY_BASE and NTFY_TOKEN):
        return  # ntfy is optional; Taildrop itself notifies the device
    try:
        requests.post(
            f"{NTFY_BASE}/{NTFY_TOPIC}",
            data=message.encode(),
            headers={
                "Authorization": f"Bearer {NTFY_TOKEN}",
                "Title": title,
                "X-Tags": tags,
            },
            timeout=15,
        )
    except Exception:
        pass  # notification failure must not fail the job


def ydl_opts(mode, outdir):
    opts = {
        "outtmpl": os.path.join(outdir, "%(title).150B.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if not LOG_CONTENT:
        opts["logger"] = _Silent()
    if mode == "audio":
        opts["format"] = "ba/b"
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"},
            {"key": "FFmpegMetadata"},
        ]
    else:
        opts["format"] = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
        opts["merge_output_format"] = "mp4"
    return opts


def set_state(job_id, state, detail=""):
    with jobs_lock:
        job = jobs[job_id]
        job["state"] = state
        job["detail"] = detail if LOG_CONTENT else ""
        if state in ("done", "error") and not LOG_CONTENT:
            job["url"] = ""
    line = f"[{job_id}] {state}"
    if LOG_CONTENT:
        line += f" mode={job['mode']} target={job['target']} url={job['url']} {detail}"
    print(line, flush=True)


def run_job(job_id, url, mode, target):
    set_state(job_id, "downloading")
    outdir = tempfile.mkdtemp(prefix="taildrop-dl-")
    try:
        # some sites 403/timeout transiently — one fresh re-extract usually clears it
        for attempt in (1, 2):
            try:
                with yt_dlp.YoutubeDL(ydl_opts(mode, outdir)) as ydl:
                    ydl.extract_info(url, download=True)
                break
            except yt_dlp.utils.DownloadError:
                if attempt == 2:
                    raise
                set_state(job_id, "retrying")

        files = [os.path.join(outdir, f) for f in os.listdir(outdir)]
        if not files:
            raise RuntimeError("yt-dlp produced no output file")

        set_state(job_id, "delivering")
        for path in files:
            subprocess.run(
                TAILSCALE + ["file", "cp", path, f"{target}:"],
                check=True, capture_output=True, text=True, timeout=3600,
            )

        notify("taildrop-dl", f"Delivered to {target} via Taildrop. Open Tailscale to save it.",
               tags="white_check_mark")
        set_state(job_id, "done")
    except subprocess.CalledProcessError as e:
        notify("taildrop-dl", "Taildrop delivery failed.", tags="x")
        set_state(job_id, "error", f"taildrop: {(e.stderr or '').strip()}")
    except Exception as e:
        notify("taildrop-dl", "Download failed.", tags="x")
        set_state(job_id, "error", str(e))
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def authed():
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip() or request.args.get("token", "")
    return token == API_TOKEN


@app.after_request
def access_log(resp):
    if request.path != "/health":
        print(f"{request.remote_addr} {request.method} {request.path} -> {resp.status_code}",
              flush=True)
    return resp


@app.post("/grab")
def grab():
    if not authed():
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    mode = (data.get("mode") or "video").strip().lower()
    # match targets case-insensitively — apps display device names with varying case
    requested = (data.get("target") or "").strip().lower()
    target = {t.lower(): t for t in TARGETS}.get(requested) if requested else TARGETS[0]
    if target is None:
        print(f"rejected: unknown target {requested!r}", flush=True)
        return jsonify(error="unknown target", allowed=TARGETS), 400
    if not url.startswith(("http://", "https://")):
        # iOS Shortcuts sometimes sends text/plain or the whole shared text — fish out a URL
        m = re.search(r"https?://\S+", request.get_data(as_text=True))
        if not m:
            print("rejected: no url in body", flush=True)
            return jsonify(error="bad url"), 400
        url = m.group(0).rstrip('"\'}')
    # coerce whatever a share-sheet menu sends ("Audio", "Music", "♪ song", …); default video
    mode = "audio" if any(w in mode for w in ("aud", "music", "song", "mp3", "m4a")) else "video"
    job_id = uuid.uuid4().hex[:8]
    with jobs_lock:
        jobs[job_id] = {"state": "queued", "url": url, "mode": mode,
                        "target": target, "detail": ""}
    executor.submit(run_job, job_id, url, mode, target)
    return jsonify(id=job_id, state="queued", mode=mode, target=target), 202


@app.get("/status/<job_id>")
def status(job_id):
    if not authed():
        return jsonify(error="unauthorized"), 401
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="unknown job"), 404
    return jsonify(id=job_id, **job)


@app.get("/health")
def health():
    return jsonify(ok=True)


if __name__ == "__main__":
    from waitress import serve
    serve(app, host=BIND, port=PORT)
