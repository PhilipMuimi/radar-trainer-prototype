# 战术雷达训练器 / Tactical Radar Trainer Prototype

Playable browser prototype for a standalone tactical radar trainer.

Live demo:

https://radar-trainer-prototype.vercel.app

## Features

- 中文优先界面，附英文翻译 / Chinese-first UI with English translations
- Browser/mobile playable radar trainer
- WASD, arrow keys, and on-screen D-pad movement
- Alpha vs Bravo round simulation
- Objective attack/defense gameplay
- Score, timer, alive counts, and event log
- Pause, next round, and difficulty controls
- Auto/manual map selection

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

This is a legal standalone trainer prototype. It does not bypass anti-cheat, hide processes, or extract protected live-game coordinates.
