# Vercel Deployment

This folder contains two versions of the prototype:

- `app.py`: local Python version, run with `python3 app.py --host 127.0.0.1 --port 8000`.
- `public/index.html`: Vercel static version, playable entirely in the browser.

Vercel is best used here for the static prototype. The local Python server uses long-running in-memory state and server-sent events, which is not a good fit for serverless hosting.

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
