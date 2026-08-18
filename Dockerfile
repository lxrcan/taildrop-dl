FROM tailscale/tailscale:latest AS ts

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# tailscale CLI only — talks to the host tailscaled over the mounted socket
COPY --from=ts /usr/local/bin/tailscale /usr/local/bin/tailscale
RUN pip install --no-cache-dir yt-dlp flask waitress requests
WORKDIR /app
COPY app.py pick_ytdlp.py ./
# on every start, move yt-dlp to the newest release ≥7 days old (fresh extractors,
# but never a day-zero release); on any failure keep the installed version
CMD ["sh", "-c", "v=$(python pick_ytdlp.py) && pip install -q --no-cache-dir yt-dlp==$v; exec python app.py"]
