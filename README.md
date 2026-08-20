# taildrop-dl

Share a link from your phone's share sheet → a small server on your tailnet downloads it
with [yt-dlp](https://github.com/yt-dlp/yt-dlp) → the file is **Taildropped** straight back
to your device. Video arrives as mp4, audio as m4a. No cloud, no accounts, no traces.

<p align="center">
  <video src="https://github.com/lxrcan/taildrop-dl/raw/main/docs/demo.mp4" controls muted loop width="360"></video>
</p>

> _Demo not playing inline? [Watch it here.](https://github.com/lxrcan/taildrop-dl/raw/main/docs/demo.mp4)_

```
iPhone share sheet ──POST /grab──▶ taildrop-dl (Docker, on your tailnet)
                                        │ yt-dlp + ffmpeg
                                        ▼
iPhone Tailscale app ◀──tailscale file cp── finished file (then deleted)
```

## Why it's nice

- **One tap from any app** — share a YouTube/SoundCloud/anything link, pick Video or
  Audio, and the file shows up in the Tailscale app ready to save to Photos/Files.
- **Private by default** — logs contain job ids and states only; URLs, titles, and
  error text are never written anywhere, and downloads are deleted the moment they're
  delivered. (Set `LOG_CONTENT=true` if you prefer debuggability.)
- **No host privileges** — the container mounts the host's tailscaled socket and runs
  the bundled `tailscale` CLI as container-root, which the LocalAPI accepts. No sudo,
  no `tailscale set --operator`, no Tailscale key inside the container.
- **Multiple recipients** — allowlist several tailnet devices (`TAILDROP_TARGETS`) and
  pick one per request; the first is the default.
- **Fresh-but-not-bleeding-edge yt-dlp** — on every container start it upgrades to the
  newest yt-dlp release that is at least `YTDLP_MIN_AGE_DAYS` (default 7) days old.

## Requirements

- A Linux box on your tailnet with Docker and a running tailscaled
  (a Raspberry Pi is plenty).
- The receiving device signed into the same tailnet (Taildrop works tailnet-internally).

## Setup

```bash
git clone https://github.com/lxrcan/taildrop-dl && cd taildrop-dl
cp .env.example .env
# edit .env: API_TOKEN (openssl rand -hex 24), TAILDROP_TARGETS, BIND_ADDR
docker compose up -d --build
curl http://$BIND_ADDR:8094/health   # {"ok":true}
```

Set `BIND_ADDR` to the server's own Tailscale IP so the API is reachable only from
your tailnet. Never bind to a publicly reachable interface — the token is the only gate.

## API

| Endpoint | Body / notes |
|---|---|
| `POST /grab` | `{"url": "...", "mode": "video"\|"audio", "target": "device"}` — `mode` and `target` optional. Returns `202 {"id": ...}`. `Authorization: Bearer <API_TOKEN>`. |
| `GET /status/<id>` | job state (`queued`/`downloading`/`retrying`/`delivering`/`done`/`error`) |
| `GET /health` | unauthenticated liveness check |

`mode` is forgiving: anything containing `aud`/`music`/`song`/`mp3`/`m4a` means audio,
everything else means video — so a share-sheet menu can send its label as-is. If the
body isn't valid JSON, the first `http(s)://` URL found in it is used.

## The auth token

The API is protected by a single shared secret. The tailnet bind (`BIND_ADDR`) is the
real security boundary — only devices on your tailnet can reach the port at all — and
the token is defense in depth on top of it: it stops other people *on your tailnet*
(shared devices, guests via node sharing) from queuing downloads or reading job status.

- Generate one: `openssl rand -hex 24`
- Put the same value in two places: `API_TOKEN=` in the server's `.env`, and the
  `Authorization: Bearer <token>` header in your Shortcut / client.
- It never leaves your tailnet, but treat it like a password anyway: don't commit it,
  and if a shared Shortcut leaks it, change it in `.env`, restart
  (`docker compose up -d`), and update your Shortcut.

## iOS Shortcut

Import the template — **[Taildrop-dl shortcut](https://www.icloud.com/shortcuts/aacd7decf653494eacc2462c3f56e944)**
— then edit two things to make it yours:

1. The request URL: `http://<your-server-tailnet-ip>:8094/grab`
2. The `Authorization` header: `Bearer <your API_TOKEN>`

Share a link from any app, pick Video or Audio, and a minute later the file is in the
Tailscale app — Save to Photos / Files from there. In the JSON body, `url` must be the
Shortcut Input variable and `mode` the literal text `video`/`audio` (put the input in
both and everything arrives as video). An optional `target` field picks another device
from your `TAILDROP_TARGETS` allowlist.

## Android

No Shortcuts app needed — anything that can turn a share into an HTTP POST works.
[MacroDroid](https://www.macrodroid.com/) is an easy option: a macro with the
**Share** trigger and an **HTTP Request** action posting the same JSON
(`Authorization: Bearer <API_TOKEN>`, `url` = shared text) to
`http://<server-tailnet-ip>:8094/grab`. Tasker or HTTP Shortcuts work the same way.
Delivered files appear in the Tailscale Android app.

## Privacy model

The point of this project is that *nothing sticks*: media is downloaded to a tmpdir
inside the container, handed to Taildrop, and deleted in a `finally`. With the default
`LOG_CONTENT=false`, yt-dlp's output is fully silenced and finished jobs keep no URL —
`docker logs` shows only lines like `[a1b2c3d4] done`. Restarting the container clears
the in-memory job table; recreating it deletes the log file itself.

## License

MIT
