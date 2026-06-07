#!/usr/bin/env python3
"""Replay authorized telemetry frames into the radar backend."""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from copy import deepcopy
from pathlib import Path
from urllib import request


MAP_SIZE = 1000
MIN_DISTANCE = 82
COLORS = ["#3b82f6", "#22c55e", "#a855f7", "#f59e0b", "#f43f5e", "#06b6d4", "#ef4444", "#84cc16"]
COLOR_NAMES = ["蓝色", "绿色", "紫色", "橙色", "红色", "青色", "玫红", "黄绿"]


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
        pairs.append((frames[-1], frames[0]))
    return pairs


def random_players(count: int) -> list[dict]:
    players = []
    spawns = [(220, 700), (285, 760), (165, 590), (380, 800), (810, 340), (845, 520), (760, 630), (720, 260)]
    for index in range(count):
        team = "alpha" if index < count / 2 else "bravo"
        sx, sy = spawns[index % len(spawns)]
        angle = random.random() * math.tau
        speed = random.uniform(42, 78)
        players.append({
            "pid": index + 1,
            "team": team,
            "color": COLORS[index % len(COLORS)],
            "color_name": COLOR_NAMES[index % len(COLOR_NAMES)],
            "weapon": ["rifle", "smg", "awp", "rifle"][index % 4],
            "hp": 100,
            "x": float(sx + random.uniform(-35, 35)),
            "y": float(sy + random.uniform(-35, 35)),
            "vx": math.cos(angle) * speed,
            "vy": math.sin(angle) * speed,
            "heading": angle,
            "alive": True,
        })
    separate_players(players)
    return players


def separate_players(players: list[dict]) -> None:
    for _ in range(3):
        for left_index, left in enumerate(players):
            for right in players[left_index + 1:]:
                dx = right["x"] - left["x"]
                dy = right["y"] - left["y"]
                distance = max(1.0, math.hypot(dx, dy))
                if distance >= MIN_DISTANCE:
                    continue
                push = (MIN_DISTANCE - distance) / 2
                nx = dx / distance
                ny = dy / distance
                left["x"] -= nx * push
                left["y"] -= ny * push
                right["x"] += nx * push
                right["y"] += ny * push
        for player in players:
            player["x"] = min(MAP_SIZE - 80, max(80, player["x"]))
            player["y"] = min(MAP_SIZE - 80, max(80, player["y"]))


def random_step(players: list[dict], dt: float) -> list[dict]:
    next_players = deepcopy(players)
    center = MAP_SIZE / 2
    for player in next_players:
        player["vx"] += random.uniform(-95, 95) * dt
        player["vy"] += random.uniform(-95, 95) * dt

        for other in next_players:
            if other["pid"] == player["pid"]:
                continue
            dx = player["x"] - other["x"]
            dy = player["y"] - other["y"]
            distance = max(1.0, math.hypot(dx, dy))
            if distance < MIN_DISTANCE * 1.35:
                force = (MIN_DISTANCE * 1.35 - distance) * 2.2
                player["vx"] += (dx / distance) * force * dt
                player["vy"] += (dy / distance) * force * dt

        player["vx"] += (center - player["x"]) * 0.025 * dt
        player["vy"] += (center - player["y"]) * 0.025 * dt

        speed = math.hypot(player["vx"], player["vy"])
        if speed < 38:
            angle = random.random() * math.tau
            player["vx"] += math.cos(angle) * 28
            player["vy"] += math.sin(angle) * 28
            speed = math.hypot(player["vx"], player["vy"])
        if speed > 115:
            player["vx"] = player["vx"] / speed * 115
            player["vy"] = player["vy"] / speed * 115

        player["x"] += player["vx"] * dt
        player["y"] += player["vy"] * dt

        if player["x"] < 80 or player["x"] > MAP_SIZE - 80:
            player["vx"] *= -0.92
            player["x"] = min(MAP_SIZE - 80, max(80, player["x"]))
        if player["y"] < 80 or player["y"] > MAP_SIZE - 80:
            player["vy"] *= -0.92
            player["y"] = min(MAP_SIZE - 80, max(80, player["y"]))

    separate_players(next_players)
    for player in next_players:
        player["heading"] = math.atan2(player["vy"], player["vx"])
        player["x"] = round(player["x"], 3)
        player["y"] = round(player["y"], 3)
        player["vx"] = round(player["vx"], 3)
        player["vy"] = round(player["vy"], 3)
        player["heading"] = round(player["heading"], 3)
    return next_players


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream recorded authorized telemetry frames.")
    parser.add_argument("--file", default="authorized_telemetry_replay.json")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/telemetry")
    parser.add_argument("--token", default="")
    parser.add_argument("--hz", default=12, type=float)
    parser.add_argument("--steps", default=8, type=int, help="Interpolated frames emitted between recorded keyframes.")
    parser.add_argument("--random", action="store_true", help="Generate random non-colliding live movement instead of replaying keyframes.")
    parser.add_argument("--players", default=8, type=int, help="Player count for --random mode.")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    interval = 1 / max(args.hz, 0.1)
    sequence = 0
    if args.random:
        players = random_players(max(2, min(args.players, 20)))
        while True:
            sequence += 1
            players = random_step(players, interval)
            payload = {
                "mode": "authorized-random",
                "map": "compact",
                "sequence": sequence,
                "players": players,
            }
            print(post_frame(args.url, args.token, payload))
            time.sleep(interval)

    replay = json.loads(Path(args.file).read_text(encoding="utf-8"))
    frames = replay.get("frames", [])
    if not frames:
        raise SystemExit("replay file must contain frames")

    steps = max(args.steps, 1)
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
