# Vercel Deployment

This folder contains two versions of the prototype:

- `app.py`: local Python version, run with `python3 app.py --host 127.0.0.1 --port 8000`.
- `public/index.html`: Vercel static version, playable entirely in the browser.

Vercel is best used here for the static prototype. The local Python server uses long-running in-memory state and server-sent events, which is not a good fit for serverless hosting.

For real authorized telemetry, deploy `app.py` on a persistent VPS/container instead of Vercel serverless. Set:

```bash
export RADAR_TELEMETRY_TOKEN='change-this-long-secret'
export RADAR_REQUIRE_TELEMETRY=1
export RADAR_ALLOWED_ORIGIN='https://your-domain.example'
python3 app.py --host 0.0.0.0 --port 8000
```

Then point your authorized telemetry producer to:

```text
https://your-domain.example/api/telemetry
```

## Deploy

1. Install Vercel CLI if needed:

```bash
npm i -g vercel
```

2. Deploy from this folder:

```bash
cd ~/Downloads/game
vercel
```

3. For production:

```bash
vercel --prod
```

## What To Submit

Give the client the Vercel URL and describe it as:

```text
Standalone tactical radar trainer prototype.
Chinese-first UI with English translations.
Playable in browser/mobile using WASD, arrow keys, or on-screen D-pad.
```
