#!/usr/bin/env python3
"""Replay authorized telemetry frames into the radar backend."""

from __future__ import annotations

import argparse
import json
import time
from copy import deepcopy
from pathlib import Path
from urllib import request


def post_frame(url: str, token: str, payload: dict) -> str:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, data=body, headers=headers, method="POST")
    with request.urlopen(req, timeout=5) as response:
        return response.read().decode("utf-8")


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def interpolate_player(start: dict, end: dict, t: float) -> dict:
    player = deepcopy(start)
    for key in ("x", "y", "vx", "vy", "heading"):
        player[key] = round(lerp(float(start.get(key, 0)), float(end.get(key, 0)), t), 3)
    player["hp"] = round(lerp(float(start.get("hp", 100)), float(end.get("hp", 100)), t))
    player["alive"] = bool(end.get("alive", True)) if t > 0.75 else bool(start.get("alive", True))
    return player


def interpolate_frame(start: dict, end: dict, t: float) -> list[dict]:
    end_by_pid = {player["pid"]: player for player in end["players"]}
    players = []
    for player in start["players"]:
        target = end_by_pid.get(player["pid"], player)
        players.append(interpolate_player(player, target, t))
    return players


def replay_route(frames: list[dict], loop: bool) -> list[tuple[dict, dict]]:
    if len(frames) == 1:
        return [(frames[0], frames[0])]
    pairs = list(zip(frames, frames[1:]))
    if loop:
        reversed_frames = list(reversed(frames))
        pairs.extend(zip(reversed_frames, reversed_frames[1:]))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream recorded authorized telemetry frames.")
    parser.add_argument("--file", default="authorized_telemetry_replay.json")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/telemetry")
    parser.add_argument("--token", default="")
    parser.add_argument("--hz", default=12, type=float)
    parser.add_argument("--steps", default=8, type=int, help="Interpolated frames emitted between recorded keyframes.")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    replay = json.loads(Path(args.file).read_text(encoding="utf-8"))
    frames = replay.get("frames", [])
    if not frames:
        raise SystemExit("replay file must contain frames")

    interval = 1 / max(args.hz, 0.1)
    steps = max(args.steps, 1)
    sequence = 0
    while True:
        for start, end in replay_route(frames, args.loop):
            for step in range(steps):
                sequence += 1
                players = interpolate_frame(start, end, step / steps)
                payload = {
                    "mode": replay.get("mode", "authorized"),
                    "map": replay.get("map", "training"),
                    "sequence": sequence,
                    "players": players,
                }
                print(post_frame(args.url, args.token, payload))
                time.sleep(interval)
        if not args.loop:
            break


if __name__ == "__main__":
    main()
