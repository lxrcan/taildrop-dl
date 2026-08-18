"""Print the newest yt-dlp version on PyPI that is at least MIN_AGE_DAYS old.

Run at container start: we want fresh extractors (YouTube breaks old ones) but not
day-zero releases. Prints nothing on any failure — caller then keeps what's installed.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

MIN_AGE_DAYS = int(os.environ.get("YTDLP_MIN_AGE_DAYS", "7"))

try:
    with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=15) as r:
        releases = json.load(r)["releases"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=MIN_AGE_DAYS)
    candidates = []
    for version, files in releases.items():
        if not files:
            continue
        uploaded = min(
            datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00"))
            for f in files
        )
        if uploaded <= cutoff:
            candidates.append((uploaded, version))
    if candidates:
        print(max(candidates)[1])
except Exception:
    sys.exit(1)
