# 战术雷达训练器 / Tactical Radar Trainer

授权实时数据雷达后台和移动端查看器 / Authorized real-time radar backend and mobile viewer.

Live demo:

https://radar-trainer-prototype.vercel.app

## Features

- 中文优先界面，附英文翻译 / Chinese-first UI with English translations
- Browser/mobile radar viewer
- Token-protected authorized telemetry ingest API
- Real-time Server-Sent Events stream
- Health and telemetry schema endpoints
- Live-only production mode with `RADAR_REQUIRE_TELEMETRY=1`
- Recorded authorized telemetry replay for demos
- Alpha vs Bravo round simulation
- Objective attack/defense gameplay
- Score, timer, alive counts, and event log
- Auto/manual map selection and map inference

## Real Data Mode

The backend accepts real coordinates from an authorized source at:

```text
POST /api/telemetry
```

Production should set a telemetry token:

```bash
export RADAR_TELEMETRY_TOKEN='change-this-long-secret'
export RADAR_REQUIRE_TELEMETRY=1
python3 app.py --host 0.0.0.0 --port 8000
```

`RADAR_REQUIRE_TELEMETRY=1` disables fake fallback players. The radar waits for authorized live telemetry and marks the feed stale if updates stop.

Send authorized telemetry:

```bash
python3 send_authorized_telemetry.py authorized_telemetry_example.json \
  --url http://127.0.0.1:8000/api/telemetry \
  --token change-this-long-secret
```

Stream a recorded authorized replay:

```bash
python3 stream_authorized_telemetry.py --token change-this-long-secret --loop
```

For random non-colliding live movement during a client demo:

```bash
python3 stream_authorized_telemetry.py --token change-this-long-secret --random --hz 20 --players 8
```

Keep this command running while the browser is open. If it stops, the page will show `过期 / Stale` after the telemetry TTL expires.

Health check:

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read().decode())"
```

Schema:

```text
http://127.0.0.1:8000/api/telemetry/schema
```

## Local Python Version

```bash
python3 app.py --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

## Vercel Version

The Vercel deployment uses the static browser prototype in:

```text
public/index.html
```

Redeploy:

```bash
/home/philip/.local/nodejs/node-v22.22.2-linux-x64/bin/vercel --prod
```

## Note

This is a legal authorized telemetry product. It does not bypass anti-cheat, hide processes, or extract protected live-game coordinates. Real data must come from a source you are allowed to instrument, export, parse, or operate.

See `CLIENT_HANDOFF.md` for the explanation to give the client.
