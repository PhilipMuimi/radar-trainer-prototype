#!/usr/bin/env python3
"""Authorized radar telemetry dashboard.

The production mode accepts authorized telemetry over HTTP. It does not read
game memory, inject code, disguise processes, or interact with anti-cheat
systems.
"""

from __future__ import annotations

import argparse
import hmac
import json
import math
import os
import random
import threading
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlparse


MAP_SIZE = 1000
TICK_SECONDS = 1 / 144
MAX_STREAM_HZ = 144
MIN_STREAM_SECONDS = 1 / MAX_STREAM_HZ
MAX_POST_BYTES = 64_000
MAX_PLAYERS = 20
EXTERNAL_TTL_SECONDS = float(os.environ.get("RADAR_EXTERNAL_TTL", "5"))
TELEMETRY_TOKEN = os.environ.get("RADAR_TELEMETRY_TOKEN", "").strip()
REQUIRE_TELEMETRY = os.environ.get("RADAR_REQUIRE_TELEMETRY", "").strip().lower() in {"1", "true", "yes", "on"}
COLORS = ["#f43f5e", "#22c55e", "#3b82f6", "#f59e0b", "#a855f7"]
REGIONS = {
    "shanghai": {"name": "Shanghai", "latency": 18},
    "beijing": {"name": "Beijing", "latency": 28},
    "guangzhou": {"name": "Guangzhou", "latency": 35},
    "chengdu": {"name": "Chengdu", "latency": 42},
    "hongkong": {"name": "Hong Kong", "latency": 48},
}
VALID_MAPS = {"training", "compact", "long"}
MAP_SIGNATURES = {
    "training": {"center": (520, 570), "spread": (820, 650)},
    "compact": {"center": (520, 520), "spread": (770, 660)},
    "long": {"center": (510, 590), "spread": (860, 520)},
}
MAP_SITES = {
    "training": {"A": (690, 745), "B": (205, 235)},
    "compact": {"A": (741, 706), "B": (251, 291)},
    "long": {"A": (822, 617), "B": (172, 367)},
}
DIFFICULTY = {
    "easy": {"aim": 0.55, "speed": 0.84, "label": "简单 / Easy"},
    "normal": {"aim": 0.78, "speed": 1.0, "label": "普通 / Normal"},
    "hard": {"aim": 1.05, "speed": 1.14, "label": "困难 / Hard"},
}


@dataclass
class Player:
    pid: int
    team: str
    color: str
    color_name: str
    weapon: str
    hp: int
    x: float
    y: float
    vx: float
    vy: float
    heading: float
    alive: bool = True
    controlled: bool = False


class MatchState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode = "scrim"
        self.source = "simulation"
        self.map_id = "training"
        self.map_confidence = 1.0
        self.map_detection = "default"
        self.external_until = 0.0
        self.last_telemetry_at = 0.0
        self.telemetry_sequence = 0
        self.tick = 0
        self.difficulty = "normal"
        self.score = {"alpha": 0, "bravo": 0}
        self.events: list[str] = []
        self.paused = False
        self.phase = "live"
        self.round_number = 0
        self.round_length = 105.0
        self.round_started_at = time.time()
        self.phase_changed_at = time.time()
        self.objective_site = "A"
        self.objective_progress = 0.0
        self.winner = ""
        self.players: list[Player] = []
        self.controlled_pid = 0
        self.input_x = 0.0
        self.input_y = 0.0
        if REQUIRE_TELEMETRY:
            self.started_at = time.time()
            self.source = "waiting"
            self.events = ["等待授权实时数据 / Waiting for authorized live telemetry"]
        else:
            self._start_round(reset_score=True)

    def _start_round(self, reset_score: bool = False) -> None:
        if reset_score:
            self.score = {"alpha": 0, "bravo": 0}
            self.round_number = 0
            self.events = []
        self.round_number += 1
        self.started_at = time.time()
        self.round_started_at = time.time()
        self.phase_changed_at = time.time()
        self.objective_site = random.choice(["A", "B"])
        self.objective_progress = 0.0
        self.phase = "live"
        self.winner = ""
        self.players = self._spawn_players()
        self._assign_controlled_player()
        self._push_event(f"第 {self.round_number} 回合 / Round {self.round_number}: 进攻方攻击 {self.objective_site} 点 / Attack site {self.objective_site}")

    def _assign_controlled_player(self) -> None:
        for player in self.players:
            player.controlled = False

    def _spawn_players(self) -> list[Player]:
        players: list[Player] = []
        color_names = ["蓝色", "绿色", "紫色", "橙色", "黄色"]
        weapons = ["pistol", "rifle", "knife", "awp", "smg"]
        spawn = {
            "training": {"alpha": (220, 730), "bravo": (810, 290)},
            "compact": {"alpha": (225, 700), "bravo": (815, 320)},
            "long": {"alpha": (180, 760), "bravo": (870, 330)},
        }.get(self.map_id, {"alpha": (220, 730), "bravo": (810, 290)})
        for team in ("alpha", "bravo"):
            base_x, base_y = spawn[team]
            for i in range(5):
                speed = random.uniform(36, 76)
                angle = random.uniform(0, math.tau)
                players.append(
                    Player(
                        pid=len(players) + 1,
                        team=team,
                        color=COLORS[i],
                        color_name=color_names[i],
                        weapon=weapons[(i + (0 if team == "alpha" else 2)) % len(weapons)],
                        hp=100,
                        x=base_x + random.uniform(-70, 70),
                        y=base_y + (i - 2) * 46 + random.uniform(-18, 18),
                        vx=math.cos(angle) * speed,
                        vy=math.sin(angle) * speed,
                        heading=angle,
                    )
                )
        return players

    def step(self) -> None:
        with self.lock:
            if self.paused:
                return
            if time.time() < self.external_until:
                return
            if self.source == "external":
                self.source = "stale"
                return
            if self.source in {"stale", "waiting"}:
                return
            if self.phase != "live":
                if time.time() - self.phase_changed_at > 4.0:
                    self._start_round()
                return

            self.tick += 1
            self._update_bots()
            for player in self.players:
                if not player.alive:
                    player.vx = 0
                    player.vy = 0
                    continue
                if random.random() < 0.012:
                    turn = random.uniform(-0.8, 0.8)
                    speed = min(98, max(24, math.hypot(player.vx, player.vy) + random.uniform(-14, 14)))
                    player.heading = (player.heading + turn) % math.tau
                    player.vx = math.cos(player.heading) * speed
                    player.vy = math.sin(player.heading) * speed

                player.x += player.vx * TICK_SECONDS
                player.y += player.vy * TICK_SECONDS

                if player.x < 45 or player.x > MAP_SIZE - 45:
                    player.vx *= -1
                    player.x = min(MAP_SIZE - 45, max(45, player.x))
                if player.y < 45 or player.y > MAP_SIZE - 45:
                    player.vy *= -1
                    player.y = min(MAP_SIZE - 45, max(45, player.y))

                player.heading = math.atan2(player.vy, player.vx)
            self._simulate_combat()
            self._update_objective()

    def command(self, payload: dict) -> dict:
        action = str(payload.get("action", ""))
        with self.lock:
            if action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused = False
            elif action == "toggle_pause":
                self.paused = not self.paused
            elif action == "reset":
                self._start_round(reset_score=True)
            elif action == "next_round":
                self._start_round()
            elif action == "difficulty":
                value = str(payload.get("value", "normal"))
                if value not in DIFFICULTY:
                    raise ValueError("unknown difficulty")
                self.difficulty = value
                self._push_event(f"难度已设置 / Difficulty: {DIFFICULTY[value]['label']}")
            elif action == "map":
                value = str(payload.get("value", "training"))
                if value not in VALID_MAPS:
                    raise ValueError("unknown map")
                self.map_id = value
                self.map_confidence = 1.0
                self.map_detection = "manual"
                self._start_round()
            elif action == "control":
                self.controlled_pid = 0
                self._assign_controlled_player()
                self._push_event("自动运行模式 / Autonomous mode")
            elif action == "input":
                self.input_x = 0.0
                self.input_y = 0.0
            else:
                raise ValueError("unknown action")
            return {"ok": True, "game": self._game_snapshot()}

    def _update_controlled_player(self) -> None:
        player = next((item for item in self.players if item.pid == self.controlled_pid), None)
        if not player or not player.alive:
            return
        magnitude = math.hypot(self.input_x, self.input_y)
        if magnitude < 0.05:
            player.vx *= 0.82
            player.vy *= 0.82
            return

        nx = self.input_x / magnitude
        ny = self.input_y / magnitude
        speed = 112
        player.vx = player.vx * 0.35 + nx * speed * 0.65
        player.vy = player.vy * 0.35 + ny * speed * 0.65
        player.heading = math.atan2(player.vy, player.vx)

    def _update_bots(self) -> None:
        sites = MAP_SITES.get(self.map_id, MAP_SITES["training"])
        target_site = sites[self.objective_site]
        diff = DIFFICULTY[self.difficulty]
        for index, player in enumerate(self.players):
            if not player.alive:
                continue
            if player.team == "bravo":
                tx, ty = target_site
                tx += (index - 7) * 18
                ty += math.sin((self.tick + index * 13) / 80) * 80
            else:
                site_values = list(sites.values())
                tx, ty = site_values[index % len(site_values)]
                tx += math.sin((self.tick + index * 31) / 110) * 140
                ty += math.cos((self.tick + index * 29) / 90) * 95

            dx = tx - player.x
            dy = ty - player.y
            distance = max(1.0, math.hypot(dx, dy))
            speed = (58 if player.team == "alpha" else 68) * diff["speed"]
            player.vx = player.vx * 0.88 + (dx / distance) * speed * 0.12
            player.vy = player.vy * 0.88 + (dy / distance) * speed * 0.12

    def _simulate_combat(self) -> None:
        diff = DIFFICULTY[self.difficulty]
        for attacker in [player for player in self.players if player.alive]:
            enemies = [
                player for player in self.players
                if player.alive and player.team != attacker.team and self._distance(attacker, player) < 135
            ]
            if not enemies:
                continue
            target = min(enemies, key=lambda player: self._distance(attacker, player))
            distance = self._distance(attacker, target)
            chance = (0.18 + (135 - distance) / 135 * 0.52) * diff["aim"] * TICK_SECONDS
            if attacker.weapon == "awp":
                chance *= 1.28
            elif attacker.weapon == "knife":
                chance *= 0.35
            if random.random() < chance:
                damage = random.randint(7, 18)
                if attacker.weapon == "awp":
                    damage += random.randint(8, 18)
                target.hp = max(0, target.hp - damage)
                if target.hp == 0:
                    target.alive = False
                    self._push_event(f"{self._team_label(attacker.team)} {attacker.color_name} 淘汰 / eliminated {self._team_label(target.team)} {target.color_name}")

    def _update_objective(self) -> None:
        alive_alpha = [player for player in self.players if player.team == "alpha" and player.alive]
        alive_bravo = [player for player in self.players if player.team == "bravo" and player.alive]
        if not alive_alpha:
            self._end_round("bravo", "进攻方清除防守 / Attackers cleared defenders")
            return
        if not alive_bravo:
            self._end_round("alpha", "防守方阻止进攻 / Defenders stopped attack")
            return

        round_time = time.time() - self.round_started_at
        if round_time >= self.round_length:
            self._end_round("alpha", "时间结束 / Time expired")
            return

        site_x, site_y = MAP_SITES.get(self.map_id, MAP_SITES["training"])[self.objective_site]
        attackers_on_site = sum(1 for player in alive_bravo if math.hypot(player.x - site_x, player.y - site_y) < 115)
        defenders_on_site = sum(1 for player in alive_alpha if math.hypot(player.x - site_x, player.y - site_y) < 135)
        delta = (attackers_on_site * 7.5 - defenders_on_site * 5.0) * TICK_SECONDS
        self.objective_progress = max(0.0, min(100.0, self.objective_progress + delta))
        if self.objective_progress >= 100:
            self._end_round("bravo", f"进攻方占领 {self.objective_site} 点 / Attackers secured site {self.objective_site}")

    def _end_round(self, winner: str, reason: str) -> None:
        if self.phase != "live":
            return
        self.phase = "ended"
        self.winner = winner
        self.score[winner] += 1
        self.phase_changed_at = time.time()
        self._push_event(reason)

    def _distance(self, a: Player, b: Player) -> float:
        return math.hypot(a.x - b.x, a.y - b.y)

    def _team_label(self, team: str) -> str:
        return "防守方 / Alpha" if team == "alpha" else "进攻方 / Bravo"

    def _push_event(self, message: str) -> None:
        self.events.insert(0, message)
        del self.events[8:]

    def ingest(self, payload: dict) -> dict:
        raw_players = payload.get("players")
        if not isinstance(raw_players, list):
            raise ValueError("payload must contain a players list")
        if len(raw_players) > MAX_PLAYERS:
            raise ValueError(f"players list cannot exceed {MAX_PLAYERS}")

        players = [self._player_from_payload(index, raw) for index, raw in enumerate(raw_players)]
        map_id, confidence, detection = self._identify_map(payload, players)
        sequence = int(payload.get("sequence", self.telemetry_sequence + 1))
        now = time.time()
        with self.lock:
            self.players = players
            self.mode = str(payload.get("mode", "external"))
            self.map_id = map_id
            self.map_confidence = confidence
            self.map_detection = detection
            self.source = "external"
            self.external_until = now + EXTERNAL_TTL_SECONDS
            self.last_telemetry_at = now
            self.telemetry_sequence = sequence
            self.tick += 1
            self._push_event(f"收到授权实时数据 / Authorized telemetry received #{sequence}")
        return {
            "ok": True,
            "players": len(players),
            "source": "external",
            "sequence": sequence,
            "map": map_id,
            "ttl_seconds": EXTERNAL_TTL_SECONDS,
        }

    def _identify_map(self, payload: dict, players: list[Player]) -> tuple[str, float, str]:
        explicit = payload.get("map") or payload.get("map_id") or payload.get("level")
        if isinstance(explicit, str):
            normalized = explicit.strip().lower()
            aliases = {
                "mirage": "training",
                "de_mirage": "training",
                "inferno": "compact",
                "de_inferno": "compact",
                "dust2": "long",
                "de_dust2": "long",
            }
            map_id = aliases.get(normalized, normalized)
            if map_id in VALID_MAPS:
                return map_id, 1.0, "telemetry"

        inferred, confidence = self._infer_map_from_positions(players)
        return inferred, confidence, "position-heuristic"

    def _infer_map_from_positions(self, players: list[Player]) -> tuple[str, float]:
        if not players:
            return self.map_id, 0.0

        xs = [player.x for player in players]
        ys = [player.y for player in players]
        center = (sum(xs) / len(xs), sum(ys) / len(ys))
        spread = (max(xs) - min(xs), max(ys) - min(ys))

        best_map = self.map_id if self.map_id in VALID_MAPS else "training"
        best_score = float("inf")
        for map_id, signature in MAP_SIGNATURES.items():
            sig_center = signature["center"]
            sig_spread = signature["spread"]
            center_score = math.hypot(center[0] - sig_center[0], center[1] - sig_center[1]) / MAP_SIZE
            spread_score = math.hypot(spread[0] - sig_spread[0], spread[1] - sig_spread[1]) / MAP_SIZE
            score = center_score + spread_score * 0.7
            if score < best_score:
                best_map = map_id
                best_score = score

        confidence = max(0.15, min(0.95, 1 - best_score))
        return best_map, round(confidence, 2)

    def _player_from_payload(self, index: int, raw: dict) -> Player:
        if not isinstance(raw, dict):
            raise ValueError("each player must be an object")

        team = str(raw.get("team", "alpha" if index < 5 else "bravo")).lower()
        if team not in {"alpha", "bravo"}:
            raise ValueError("player team must be alpha or bravo")
        color = str(raw.get("color", COLORS[index % len(COLORS)]))
        color_name = str(raw.get("color_name", ["红色", "绿色", "蓝色", "橙色", "紫色"][index % 5]))
        heading = float(raw.get("heading", 0.0))
        vx = float(raw.get("vx", 0.0))
        vy = float(raw.get("vy", 0.0))
        pid = int(raw.get("pid", index + 1))
        if pid < 1:
            raise ValueError("player pid must be positive")
        return Player(
            pid=pid,
            team=team,
            color=color,
            color_name=color_name,
            weapon=str(raw.get("weapon", "rifle")),
            hp=max(0, min(100, int(raw.get("hp", 100)))),
            x=max(0.0, min(float(raw.get("x", MAP_SIZE / 2)), float(MAP_SIZE))),
            y=max(0.0, min(float(raw.get("y", MAP_SIZE / 2)), float(MAP_SIZE))),
            vx=vx,
            vy=vy,
            heading=heading,
            alive=bool(raw.get("alive", True)),
        )

    def snapshot(self, region: str) -> dict:
        with self.lock:
            relay = REGIONS.get(region, REGIONS["shanghai"])
            return {
                "map": {
                    "width": MAP_SIZE,
                    "height": MAP_SIZE,
                    "id": self.map_id,
                    "confidence": self.map_confidence,
                    "detection": self.map_detection,
                },
                "mode": self.mode,
                "source": self.source,
                "game": self._game_snapshot(),
                "tick": self.tick,
                "elapsed": round(time.time() - self.started_at, 1),
                "relay": {
                    "id": region if region in REGIONS else "shanghai",
                    "name": relay["name"],
                    "latency": relay["latency"] + random.randint(-4, 7),
                },
                "telemetry": {
                    "required_live": REQUIRE_TELEMETRY,
                    "required_token": bool(TELEMETRY_TOKEN),
                    "last_received_at": self.last_telemetry_at,
                    "age_seconds": round(time.time() - self.last_telemetry_at, 3) if self.last_telemetry_at else None,
                    "sequence": self.telemetry_sequence,
                    "ttl_seconds": EXTERNAL_TTL_SECONDS,
                },
                "players": [asdict(player) for player in self.players],
            }

    def _game_snapshot(self) -> dict:
        elapsed = time.time() - self.round_started_at
        return {
            "round": self.round_number,
            "phase": self.phase,
            "paused": self.paused,
            "winner": self.winner,
            "difficulty": self.difficulty,
            "score": self.score,
            "objective": {
                "site": self.objective_site,
                "progress": round(self.objective_progress, 1),
                "time_left": max(0, round(self.round_length - elapsed, 1)),
            },
            "alive": {
                "alpha": sum(1 for player in self.players if player.team == "alpha" and player.alive),
                "bravo": sum(1 for player in self.players if player.team == "bravo" and player.alive),
            },
            "events": list(self.events),
            "controlled_pid": self.controlled_pid,
        }

    def reset(self) -> None:
        with self.lock:
            self.started_at = time.time()
            self.players = []
            self.mode = "authorized" if REQUIRE_TELEMETRY else "scrim"
            self.source = "waiting" if REQUIRE_TELEMETRY else "simulation"
            self.map_id = "training"
            self.map_confidence = 1.0
            self.map_detection = "default"
            self.external_until = 0.0
            self.last_telemetry_at = 0.0
            self.telemetry_sequence = 0
            if REQUIRE_TELEMETRY:
                self.score = {"alpha": 0, "bravo": 0}
                self.round_number = 0
                self.phase = "live"
                self.events = ["等待授权实时数据 / Waiting for authorized live telemetry"]
            else:
                self._start_round(reset_score=True)
            self.tick = 0


def telemetry_schema() -> dict:
    return {
        "name": "authorized-radar-telemetry",
        "description": "授权数据源上传的实时雷达数据 / Real-time radar data from an authorized source",
        "required_headers": {
            "Authorization": "Bearer <RADAR_TELEMETRY_TOKEN>",
            "Content-Type": "application/json",
        },
        "payload": {
            "mode": "authorized",
            "map": "training|compact|long or an alias such as de_mirage",
            "sequence": 1,
            "players": [
                {
                    "pid": 1,
                    "team": "alpha|bravo",
                    "color": "#3b82f6",
                    "color_name": "蓝色",
                    "weapon": "rifle",
                    "hp": 100,
                    "x": 260,
                    "y": 420,
                    "vx": 0,
                    "vy": 0,
                    "heading": 0.0,
                    "alive": True,
                }
            ],
        },
        "limits": {
            "max_players": MAX_PLAYERS,
            "coordinate_space": f"0..{MAP_SIZE}",
            "max_body_bytes": MAX_POST_BYTES,
            "external_ttl_seconds": EXTERNAL_TTL_SECONDS,
        },
    }


STATE = MatchState()


def simulation_loop() -> None:
    while True:
        STATE.step()
        time.sleep(TICK_SECONDS)


class RadarHandler(BaseHTTPRequestHandler):
    server_version = "AuthorizedRadar/1.0"
    protocol_version = "HTTP/1.1"
    css: ClassVar[str] = ""
    js: ClassVar[str] = ""

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_common_headers("text/plain; charset=utf-8", 0)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(HTTPStatus.OK, HTML, "text/html; charset=utf-8")
        elif parsed.path == "/styles.css":
            self._send(HTTPStatus.OK, CSS, "text/css; charset=utf-8")
        elif parsed.path == "/app.js":
            self._send(HTTPStatus.OK, JS, "application/javascript; charset=utf-8")
        elif parsed.path == "/favicon.ico":
            self._send(HTTPStatus.NO_CONTENT, "", "image/x-icon")
        elif parsed.path == "/api/state":
            region = parse_qs(parsed.query).get("region", ["shanghai"])[0]
            self._send_json(STATE.snapshot(region))
        elif parsed.path == "/api/health":
            self._send_json(self._health())
        elif parsed.path == "/api/telemetry/schema":
            self._send_json(telemetry_schema())
        elif parsed.path == "/events":
            self._events(parsed)
        else:
            self._send(HTTPStatus.NOT_FOUND, "Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/reset":
            STATE.reset()
            self._send_json({"ok": True})
        elif parsed.path == "/api/game":
            try:
                payload = self._read_json_body()
                self._send_json(STATE.command(payload))
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, str(exc), "text/plain; charset=utf-8")
        elif parsed.path == "/api/telemetry":
            try:
                self._require_telemetry_auth()
                payload = self._read_json_body()
                self._send_json(STATE.ingest(payload))
            except PermissionError as exc:
                self._send(HTTPStatus.UNAUTHORIZED, str(exc), "text/plain; charset=utf-8")
            except ValueError as exc:
                self._send(HTTPStatus.BAD_REQUEST, str(exc), "text/plain; charset=utf-8")
        else:
            self._send(HTTPStatus.NOT_FOUND, "Not found", "text/plain; charset=utf-8")

    def log_message(self, fmt: str, *args: object) -> None:
        if self.path.startswith(("/events", "/api/game", "/favicon.ico")):
            return
        print("%s - %s" % (self.address_string(), fmt % args))

    def _send(self, status: HTTPStatus, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self._send_common_headers(content_type, len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_common_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", os.environ.get("RADAR_ALLOWED_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Radar-Token")

    def _send_json(self, payload: dict) -> None:
        self._send(HTTPStatus.OK, json.dumps(payload), "application/json; charset=utf-8")

    def _require_telemetry_auth(self) -> None:
        if not TELEMETRY_TOKEN:
            return
        authorization = self.headers.get("Authorization", "")
        bearer = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
        header_token = self.headers.get("X-Radar-Token", "").strip()
        if hmac.compare_digest(bearer, TELEMETRY_TOKEN) or hmac.compare_digest(header_token, TELEMETRY_TOKEN):
            return
        raise PermissionError("missing or invalid telemetry token")

    def _health(self) -> dict:
        snapshot = STATE.snapshot("shanghai")
        return {
            "ok": True,
            "service": "authorized-radar",
            "source": snapshot["source"],
            "map": snapshot["map"],
            "players": len(snapshot["players"]),
            "telemetry": snapshot["telemetry"],
        }

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("missing request body")
        if length > MAX_POST_BYTES:
            raise ValueError("request body is too large")

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _events(self, parsed) -> None:
        query = parse_qs(parsed.query)
        region = query.get("region", ["shanghai"])[0]
        try:
            hz = int(query.get("hz", ["30"])[0])
        except ValueError:
            hz = 30
        interval = max(MIN_STREAM_SECONDS, 1 / max(1, min(hz, MAX_STREAM_HZ)))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(STATE.snapshot(region))
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError):
            return


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>战术雷达训练器 / Tactical Radar Trainer</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <main class="phone">
    <header class="topbar">
      <div class="clock" id="clock">00:00</div>
      <div class="system-pill"><span></span><strong>已连接</strong></div>
      <div class="battery">27</div>
    </header>

    <div class="actions">
      <button id="serverButton" class="icon-button" type="button" aria-label="雷达服务器">▤</button>
      <button id="settingsButton" class="icon-button" type="button" aria-label="设置">⚙</button>
    </div>

    <section class="radar-stage" aria-label="雷达">
      <canvas id="radar" width="1000" height="1000"></canvas>
    </section>

    <section class="game-hud" aria-label="训练状态">
      <div class="scoreline">
        <strong id="alphaScore">0</strong>
        <span id="roundLabel">第1回合 / R1</span>
        <strong id="bravoScore">0</strong>
      </div>
      <div class="objective">
        <div>
          <span id="objectiveSite">A 点 / Site A</span>
          <small id="timeLeft">105.0s</small>
        </div>
        <progress id="objectiveProgress" value="0" max="100"></progress>
      </div>
      <div class="alive-line">
        <span id="alphaAlive">防守方 / Alpha 5</span>
        <span id="phaseLabel">进行中 / LIVE</span>
        <span id="bravoAlive">进攻方 / Bravo 5</span>
      </div>
    </section>

    <section class="game-controls" aria-label="训练控制">
      <button id="pauseGame" type="button">暂停 / Pause</button>
      <button id="nextRound" type="button">下一回合 / Next</button>
      <select id="difficulty">
        <option value="easy">简单 / Easy</option>
        <option value="normal" selected>普通 / Normal</option>
        <option value="hard">困难 / Hard</option>
      </select>
    </section>

    <section class="play-panel" aria-label="玩家控制">
      <label>
        <span>控制 / Control</span>
        <select id="controlPlayer">
          <option value="1">防守方 / Alpha 蓝色</option>
        </select>
      </label>
      <div class="hint">移动 / Move: WASD / Arrow keys</div>
    </section>

    <section class="dpad" aria-label="移动控制">
      <button data-dir="up" type="button">▲</button>
      <button data-dir="left" type="button">◀</button>
      <button data-dir="down" type="button">▼</button>
      <button data-dir="right" type="button">▶</button>
    </section>

    <section class="readout" id="readout" aria-label="观战列表"></section>
    <section class="event-log" id="eventLog" aria-label="事件记录"></section>

    <div class="sheet hidden" id="settingsSheet" role="dialog" aria-label="设置">
      <div class="sheet-card settings-card">
        <label class="field">
          <span>选择你的昵称:</span>
          <select id="focusPlayer">
            <option value="">加载玩家列表中</option>
          </select>
        </label>

        <label class="field">
          <span>地图:</span>
          <select id="mapChoice">
            <option value="auto" selected>自动识别</option>
            <option value="training">训练图</option>
            <option value="compact">紧凑图</option>
            <option value="long">长廊图</option>
          </select>
        </label>

        <div class="row compact">
          <span>刷新率 <b>(?)</b></span>
          <label><input type="radio" name="hz" value="30">30hz</label>
          <label><input type="radio" name="hz" value="60">60hz</label>
          <label><input type="radio" name="hz" value="144" checked>144hz</label>
        </div>

        <label class="slider-row"><span>平滑 <b>(?)</b></span><input id="smooth" type="range" min="0" max="100" value="95"></label>
        <label class="slider-row"><span>玩家大小 <b>(?)</b></span><input id="playerSize" type="range" min="8" max="30" value="18"></label>

        <div class="segmented" aria-label="指示器">
          <span>指示器</span>
          <button class="selected" type="button" data-shape="cone">◆</button>
          <button type="button" data-shape="triangle">▲</button>
          <button type="button" data-shape="bar">▌</button>
        </div>

        <div class="toggles">
          <label>昵称 <input id="showNames" type="checkbox" checked></label>
          <label>队友 <input id="showFriends" type="checkbox" checked></label>
          <label>武器 <input id="showWeapons" type="checkbox" checked></label>
          <label>道具轨迹 <input id="showTrails" type="checkbox"></label>
          <label>跟随视角旋转 <input id="rotateView" type="checkbox"></label>
        </div>

        <label class="slider-row"><span>缩放</span><input id="zoom" type="range" min="70" max="150" value="100"></label>

        <div class="color-row"><span>友方颜色</span><button class="swatch friend" type="button" aria-label="友方颜色"></button></div>
        <div class="color-row"><span>敌人颜色</span><button class="swatch enemy" type="button" aria-label="敌人颜色"></button></div>

        <div class="toggles">
          <label>观战人数 <input id="showCount" type="checkbox"></label>
          <label>观战列表 <input id="showList" type="checkbox" checked></label>
        </div>

        <p class="warning">请使用授权数据源或训练数据打开，否则无效。</p>
      </div>
    </div>

    <div class="sheet hidden" id="serverSheet" role="dialog" aria-label="雷达服务器">
      <div class="sheet-card server-card">
        <div class="server-title">
          <strong>雷达服务器</strong>
          <span>仅供参考，延迟高不一定卡</span>
        </div>
        <div class="server-graphic"></div>
        <label class="field">
          <span>线路</span>
          <select id="region">
          <option value="shanghai">上海 / Shanghai</option>
          <option value="beijing">北京 / Beijing</option>
          <option value="guangzhou">广州 / Guangzhou</option>
          <option value="chengdu">成都 / Chengdu</option>
          <option value="hongkong">香港 / Hong Kong</option>
          </select>
        </label>
        <div class="server-actions">
          <button id="reset" type="button">·</button>
          <button id="closeServer" type="button">✓</button>
        </div>
      </div>
    </div>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


CSS = """
:root {
  color-scheme: dark;
  --bg: #282b37;
  --card: #20222c;
  --card-2: #242733;
  --line: #343746;
  --text: #f7f7fb;
  --muted: #a9adbb;
  --danger: #ff3f6b;
  --friend: #79a9ff;
  --enemy: #ffc23d;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  overflow: hidden;
}

.phone {
  position: relative;
  width: min(100vw, 620px);
  min-height: 100vh;
  margin: 0 auto;
  background: #282b37;
  overflow: hidden;
}

.topbar {
  position: fixed;
  top: 18px;
  left: 50%;
  z-index: 5;
  width: min(100vw, 620px);
  transform: translateX(-50%);
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding: 0 34px;
  pointer-events: none;
}

.clock {
  font-size: 26px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}

.system-pill {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 180px;
  justify-content: center;
  padding: 10px 22px;
  border-radius: 999px;
  background: #050506;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.25);
}

.system-pill span {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #15d17b;
  box-shadow: 0 0 0 7px rgba(21, 209, 123, 0.16);
}

.system-pill strong {
  font-size: 18px;
}

.battery {
  justify-self: end;
  min-width: 36px;
  padding: 2px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.8);
  color: #2a2d38;
  font-size: 18px;
  font-weight: 900;
  text-align: center;
}

.actions {
  position: fixed;
  z-index: 6;
  top: 112px;
  right: max(24px, calc((100vw - 620px) / 2 + 24px));
  display: flex;
  gap: 20px;
}

.icon-button {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: white;
  font-size: 30px;
  cursor: pointer;
}

.radar-stage {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 160px 18px 164px;
}

canvas {
  width: min(92vw, 560px);
  max-width: 100%;
  aspect-ratio: 1;
  display: block;
  touch-action: none;
}

.game-hud {
  position: fixed;
  top: 82px;
  left: 50%;
  z-index: 4;
  width: min(92vw, 560px);
  transform: translateX(-50%);
  display: grid;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  background: rgba(27, 30, 40, 0.74);
  backdrop-filter: blur(12px);
}

.scoreline,
.alive-line {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 10px;
}

.scoreline strong {
  font-size: 24px;
  line-height: 1;
}

.scoreline strong:last-child,
.alive-line span:last-child {
  text-align: right;
}

.scoreline span,
.alive-line span,
.objective small {
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}

.objective {
  display: grid;
  grid-template-columns: 72px 1fr;
  align-items: center;
  gap: 10px;
}

.objective div {
  display: grid;
  gap: 2px;
}

#objectiveSite {
  font-size: 18px;
  font-weight: 900;
}

progress {
  width: 100%;
  height: 10px;
  border: 0;
  border-radius: 999px;
  overflow: hidden;
  background: #11131a;
}

progress::-webkit-progress-bar {
  background: #11131a;
}

progress::-webkit-progress-value {
  background: linear-gradient(90deg, #79a9ff, #ffc23d);
}

progress::-moz-progress-bar {
  background: linear-gradient(90deg, #79a9ff, #ffc23d);
}

.game-controls {
  display: none;
  position: fixed;
  left: 50%;
  bottom: 122px;
  z-index: 5;
  width: min(54vw, 340px);
  transform: translateX(-50%);
  display: grid;
  grid-template-columns: 1fr 1fr 1.2fr;
  gap: 8px;
}

.game-controls button,
.game-controls select {
  min-height: 38px;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 6px;
  background: rgba(22, 24, 32, 0.88);
  color: white;
  font: inherit;
  font-weight: 850;
}

.play-panel {
  display: none;
  position: fixed;
  left: max(18px, calc((100vw - 620px) / 2 + 18px));
  bottom: 118px;
  z-index: 5;
  display: grid;
  gap: 6px;
  width: min(35vw, 180px);
  padding: 8px;
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 8px;
  background: rgba(22, 24, 32, 0.84);
  backdrop-filter: blur(10px);
}

.play-panel label {
  display: grid;
  gap: 4px;
}

.play-panel span,
.hint {
  color: var(--muted);
  font-size: 11px;
  font-weight: 850;
  text-transform: uppercase;
}

.play-panel select {
  min-height: 34px;
  border-width: 1px;
  font-size: 13px;
}

.dpad {
  display: none;
  position: fixed;
  right: max(18px, calc((100vw - 620px) / 2 + 18px));
  bottom: 112px;
  z-index: 5;
  display: grid;
  grid-template-columns: repeat(3, 44px);
  grid-template-rows: repeat(3, 38px);
  gap: 4px;
}

.dpad button {
  border: 1px solid rgba(255,255,255,0.14);
  border-radius: 8px;
  background: rgba(17, 19, 26, 0.86);
  color: white;
  font-size: 18px;
  font-weight: 900;
  touch-action: none;
}

.dpad [data-dir="up"] { grid-column: 2; grid-row: 1; }
.dpad [data-dir="left"] { grid-column: 1; grid-row: 2; }
.dpad [data-dir="down"] { grid-column: 2; grid-row: 3; }
.dpad [data-dir="right"] { grid-column: 3; grid-row: 2; }

.game-controls,
.play-panel,
.dpad {
  display: none !important;
}

.readout {
  position: fixed;
  left: 50%;
  bottom: 22px;
  z-index: 4;
  width: min(92vw, 560px);
  transform: translateX(-50%);
  display: grid;
  gap: 8px;
  pointer-events: none;
}

.readout.hidden-list {
  display: none;
}

.readout-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
  color: white;
  font-size: clamp(17px, 5vw, 26px);
  line-height: 1.05;
  text-shadow: 0 2px 3px rgba(0, 0, 0, 0.8);
  white-space: nowrap;
}

.readout-row i {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  border-radius: 50%;
}

.hp {
  color: #1ee032;
  font-weight: 750;
}

.event-log {
  position: fixed;
  right: max(18px, calc((100vw - 620px) / 2 + 18px));
  top: 178px;
  z-index: 4;
  display: grid;
  gap: 5px;
  width: min(50vw, 240px);
  pointer-events: none;
}

.event-item {
  overflow: hidden;
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(13, 15, 21, 0.62);
  color: rgba(255,255,255,0.88);
  font-size: 12px;
  font-weight: 750;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sheet {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: grid;
  place-items: center;
  padding: 92px 22px 90px;
  background: rgba(40, 43, 55, 0.42);
}

.sheet.hidden {
  display: none;
}

.sheet-card {
  width: min(100%, 455px);
  border: 1px solid rgba(255,255,255,0.04);
  border-radius: 20px;
  background: rgba(31, 33, 44, 0.96);
  box-shadow: 0 30px 70px rgba(0, 0, 0, 0.32);
}

.settings-card {
  display: grid;
  gap: 14px;
  padding: 28px;
}

.field {
  display: grid;
  gap: 10px;
}

.field span, .row > span, .slider-row span, .segmented span, .color-row span {
  color: var(--text);
  font-size: 18px;
  font-weight: 750;
}

b {
  color: #777b8e;
}

select {
  width: 100%;
  min-height: 48px;
  padding: 0 16px;
  border: 2px solid #edf0fb;
  border-radius: 8px;
  background: #242733;
  color: white;
  font: inherit;
  font-weight: 800;
}

.row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.compact {
  flex-wrap: wrap;
}

.compact label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 800;
}

input[type="radio"], input[type="checkbox"] {
  width: 24px;
  height: 24px;
  accent-color: #edf0fb;
}

.slider-row {
  display: grid;
  grid-template-columns: 110px 1fr;
  align-items: center;
  gap: 14px;
}

input[type="range"] {
  width: 100%;
  accent-color: #f4f1eb;
}

.segmented {
  display: grid;
  grid-template-columns: 110px repeat(3, 1fr);
  align-items: center;
  gap: 8px;
}

.segmented button {
  min-height: 42px;
  border: 0;
  border-radius: 10px;
  background: #1f212b;
  color: white;
  font-size: 22px;
  cursor: pointer;
}

.segmented button.selected {
  background: #3b4056;
}

.toggles {
  display: grid;
  gap: 13px;
}

.toggles label, .color-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 18px;
  font-weight: 800;
}

.swatch {
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 50%;
}

.swatch.friend { background: var(--friend); }
.swatch.enemy { background: var(--enemy); }

.warning {
  margin: 6px 0 0;
  padding: 16px;
  border-radius: 10px;
  background: #252633;
  color: var(--danger);
  font-weight: 800;
}

.server-card {
  display: grid;
  gap: 22px;
  padding: 30px;
}

.server-title {
  display: flex;
  align-items: baseline;
  gap: 24px;
  flex-wrap: wrap;
}

.server-title strong {
  font-size: 30px;
}

.server-title span {
  color: var(--muted);
  font-size: 18px;
  font-weight: 800;
}

.server-graphic {
  height: 110px;
  border-radius: 24px;
  background:
    linear-gradient(90deg, rgba(255,255,255,0.04), rgba(255,255,255,0)),
    #20222c;
  position: relative;
  overflow: hidden;
}

.server-graphic::before,
.server-graphic::after {
  content: "";
  position: absolute;
  border-radius: 10px;
  background: rgba(255,255,255,0.035);
}

.server-graphic::before {
  left: 36px;
  top: 34px;
  width: 150px;
  height: 26px;
}

.server-graphic::after {
  right: 80px;
  top: 36px;
  width: 50px;
  height: 50px;
  border-radius: 50%;
}

.server-actions {
  display: flex;
  justify-content: center;
  gap: 28px;
}

.server-actions button {
  width: 56px;
  height: 56px;
  border: 0;
  border-radius: 50%;
  background: #2d303e;
  color: white;
  font-size: 32px;
  font-weight: 900;
  cursor: pointer;
}

@media (max-width: 420px) {
  .topbar {
    padding: 0 18px;
  }

  .clock {
    font-size: 22px;
  }

  .system-pill {
    min-width: 136px;
    padding: 9px 16px;
  }

  .system-pill strong {
    font-size: 15px;
  }

  .settings-card {
    padding: 22px;
  }
}

@media (min-width: 900px) {
  body {
    overflow: hidden;
  }

  .phone {
    width: 100vw;
    min-height: 100vh;
    margin: 0;
  }

  .topbar {
    top: 14px;
    width: min(100vw, 1120px);
    padding: 0 18px;
  }

  .actions {
    top: 122px;
    right: calc(50vw - 330px);
    gap: 12px;
  }

  .radar-stage {
    position: fixed;
    inset: 0;
    padding: 128px 300px 118px 300px;
    place-items: center;
  }

  canvas {
    width: min(58vw, 760px, calc(100vh - 210px));
    max-width: none;
  }

  .game-hud {
    top: 74px;
    width: min(58vw, 560px);
  }

  .event-log {
    top: 190px;
    right: calc(50vw - 650px);
    width: 250px;
    max-width: 250px;
  }

  .event-item {
    white-space: normal;
    line-height: 1.25;
  }

  .readout {
    left: 50%;
    bottom: 18px;
    width: 560px;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    transform: translateX(-50%);
  }

  .readout-row {
    min-width: 0;
    font-size: 18px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}
"""


JS = """
const canvas = document.getElementById('radar');
const ctx = canvas.getContext('2d');
const region = document.getElementById('region');
const readout = document.getElementById('readout');
const eventLog = document.getElementById('eventLog');
const reset = document.getElementById('reset');
const clock = document.getElementById('clock');
const alphaScore = document.getElementById('alphaScore');
const bravoScore = document.getElementById('bravoScore');
const roundLabel = document.getElementById('roundLabel');
const objectiveSite = document.getElementById('objectiveSite');
const objectiveProgress = document.getElementById('objectiveProgress');
const timeLeft = document.getElementById('timeLeft');
const alphaAlive = document.getElementById('alphaAlive');
const bravoAlive = document.getElementById('bravoAlive');
const phaseLabel = document.getElementById('phaseLabel');
const pauseGame = document.getElementById('pauseGame');
const nextRound = document.getElementById('nextRound');
const difficulty = document.getElementById('difficulty');
const controlPlayer = document.getElementById('controlPlayer');
const settingsButton = document.getElementById('settingsButton');
const serverButton = document.getElementById('serverButton');
const settingsSheet = document.getElementById('settingsSheet');
const serverSheet = document.getElementById('serverSheet');
const closeServer = document.getElementById('closeServer');
const focusPlayer = document.getElementById('focusPlayer');
const showNames = document.getElementById('showNames');
const showFriends = document.getElementById('showFriends');
const showWeapons = document.getElementById('showWeapons');
const showTrails = document.getElementById('showTrails');
const rotateView = document.getElementById('rotateView');
const showList = document.getElementById('showList');
const zoom = document.getElementById('zoom');
const playerSize = document.getElementById('playerSize');
const smooth = document.getElementById('smooth');
const mapChoice = document.getElementById('mapChoice');

let source;
let state;
let displayedPlayers = [];
let history = new Map();
let shape = 'cone';
let readoutKey = '';
let lastFrame = 0;
let mapCache;
let cachedMap = '';
let activeMap = 'training';
let inputState = {up: false, down: false, left: false, right: false};
let lastInputSent = '';
let lastInputAt = 0;

const mapLayouts = {
  training: {
    sites: [[150,180,110,100,'B'], [640,705,170,115,'A']],
    zones: [[70,670,120,115], [805,210,92,170], [820,410,85,145]],
    cover: [[185,210,38,28], [300,125,88,22], [420,390,150,20], [365,545,36,115], [545,620,150,22], [735,500,38,92], [710,760,56,28], [230,690,70,22]],
    polys: [
      [[70,120],[310,95],[365,140],[355,300],[250,325],[255,430],[135,445],[110,360],[70,360]],
      [[335,105],[560,105],[610,160],[575,245],[390,245],[390,195],[335,190]],
      [[370,310],[610,305],[640,390],[610,455],[360,455],[335,385]],
      [[645,145],[850,145],[910,235],[865,395],[740,400],[705,310],[635,280]],
      [[750,390],[905,400],[925,585],[805,665],[700,610],[720,470]],
      [[110,455],[305,455],[345,620],[275,750],[70,795],[60,675],[155,615]],
      [[345,520],[560,520],[615,650],[550,790],[365,775],[315,650]],
      [[600,655],[865,655],[900,825],[705,895],[585,820]],
      [[815,470],[950,470],[950,760],[880,825],[835,650]]
    ]
  },
  compact: {
    sites: [[205,245,92,92,'B'], [695,660,92,92,'A']],
    zones: [[410,390,170,190], [205,650,150,95]],
    cover: [[235,390,120,20], [385,285,45,120], [585,435,160,24], [305,605,40,110], [645,755,145,22]],
    polys: [
      [[135,185],[385,180],[430,310],[340,390],[145,385]],
      [[395,250],[610,250],[610,405],[555,460],[420,455]],
      [[615,320],[880,350],[870,560],[705,560],[650,460]],
      [[165,455],[385,470],[420,655],[335,785],[145,720]],
      [[425,515],[650,510],[690,685],[610,820],[430,795]],
      [[665,600],[875,610],[905,815],[705,840]]
    ]
  },
  long: {
    sites: [[130,325,84,84,'B'], [780,575,84,84,'A']],
    zones: [[555,325,165,90], [250,675,150,105]],
    cover: [[270,350,210,20], [500,410,35,180], [605,585,150,24], [350,760,260,20], [795,455,26,95]],
    polys: [
      [[70,280],[245,270],[250,445],[95,480]],
      [[245,325],[515,320],[540,430],[260,470]],
      [[515,345],[765,350],[825,470],[760,555],[550,515]],
      [[725,500],[930,520],[940,710],[790,760],[700,640]],
      [[170,545],[390,560],[435,710],[345,870],[130,810]],
      [[395,645],[705,650],[735,795],[610,900],[395,865]]
    ]
  }
};

function connect() {
  if (source) source.close();
  const hz = document.querySelector('input[name="hz"]:checked')?.value || '30';
  source = new EventSource(`/events?region=${encodeURIComponent(region.value)}&hz=${encodeURIComponent(hz)}`);
  source.onmessage = (event) => {
    state = JSON.parse(event.data);
    const detectedMap = state.map?.id && mapLayouts[state.map.id] ? state.map.id : 'training';
    if (mapChoice.value === 'auto' && activeMap !== detectedMap) {
      activeMap = detectedMap;
      cachedMap = '';
    }
    if (!displayedPlayers.length) {
      displayedPlayers = state.players.map((player) => ({...player}));
    }
    sampleTrails();
    updateControls();
    updateReadout();
    updateHud();
  };
}

function selectedMapId() {
  if (mapChoice.value === 'auto') {
    return activeMap;
  }
  return mapLayouts[mapChoice.value] ? mapChoice.value : 'training';
}

function updateClock() {
  const now = new Date();
  clock.textContent = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
}

function updateControls() {
  if (!state) return;
  const options = state.players.map((player) => {
    const team = player.team === 'alpha' ? '防守方 / Alpha' : '进攻方 / Bravo';
    return `<option value="${player.pid}">${team} ${player.color_name}</option>`;
  }).join('');
  if (focusPlayer.options.length !== state.players.length) focusPlayer.innerHTML = options;
  if (controlPlayer.options.length !== state.players.length) controlPlayer.innerHTML = options;
  if (state.game?.controlled_pid) controlPlayer.value = String(state.game.controlled_pid);
}

function updateReadout() {
  if (!state) return;
  readout.classList.toggle('hidden-list', !showList.checked);
  const players = state.players.filter((player) => showFriends.checked || player.team !== 'alpha');
  const html = players.slice(0, 5).map((player) => {
    const team = player.team === 'alpha' ? '防守' : '进攻';
    const label = showNames.checked ? `${team} ${player.color_name}` : `${team}`;
    return `<div class="readout-row"><i style="background:${player.color}"></i><span>${label}</span><span class="hp">HP:[${player.hp}]</span></div>`;
  }).join('');
  if (html !== readoutKey) {
    readout.innerHTML = html;
    readoutKey = html;
  }
}

function updateHud() {
  if (!state?.game) return;
  const game = state.game;
  alphaScore.textContent = game.score.alpha;
  bravoScore.textContent = game.score.bravo;
  roundLabel.textContent = `第${game.round}回合 / R${game.round}`;
  objectiveSite.textContent = `${game.objective.site} 点 / Site ${game.objective.site}`;
  objectiveProgress.value = game.objective.progress;
  timeLeft.textContent = `${game.objective.time_left.toFixed(1)}s`;
  alphaAlive.textContent = `防守方 / Alpha ${game.alive.alpha}`;
  bravoAlive.textContent = `进攻方 / Bravo ${game.alive.bravo}`;
  phaseLabel.textContent = game.paused ? '暂停 / PAUSED' : phaseText(game.phase);
  pauseGame.textContent = game.paused ? '继续 / Resume' : '暂停 / Pause';
  if (difficulty.value !== game.difficulty) difficulty.value = game.difficulty;

  eventLog.innerHTML = game.events.slice(0, 4).map((event) => (
    `<div class="event-item">${escapeHtml(event)}</div>`
  )).join('');
}

function phaseText(phase) {
  if (phase === 'live') return '进行中 / LIVE';
  if (phase === 'ended') return '结束 / ENDED';
  return `${phase} / ${String(phase).toUpperCase()}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function gameCommand(action, extra = {}) {
  const response = await fetch('/api/game', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action, ...extra})
  });
  if (!response.ok) {
    console.warn(await response.text());
  }
}

function inputVector() {
  let x = 0;
  let y = 0;
  if (inputState.left) x -= 1;
  if (inputState.right) x += 1;
  if (inputState.up) y -= 1;
  if (inputState.down) y += 1;
  if (x && y) {
    x *= Math.SQRT1_2;
    y *= Math.SQRT1_2;
  }
  return {x, y};
}

function sendInput(force = false) {
  const vector = inputVector();
  const key = `${vector.x.toFixed(2)},${vector.y.toFixed(2)}`;
  const now = performance.now();
  if (key === lastInputSent && (!force || now - lastInputAt < 1200)) return;
  lastInputSent = key;
  lastInputAt = now;
  gameCommand('input', vector);
}

function setInput(name, value) {
  if (inputState[name] === value) return;
  inputState[name] = value;
  sendInput(true);
}

function sampleTrails() {
  if (!state || !showTrails.checked) return;
  for (const player of state.players) {
    const key = `${player.pid}`;
    const points = history.get(key) || [];
    points.push([player.x, player.y]);
    if (points.length > 34) points.shift();
    history.set(key, points);
  }
}

function renderPlayers() {
  return displayedPlayers.length ? displayedPlayers : (state ? state.players : []);
}

function approachAngle(from, to, amount) {
  const delta = Math.atan2(Math.sin(to - from), Math.cos(to - from));
  return from + delta * amount;
}

function animatePlayers(dt) {
  if (!state) return;
  const smoothing = Number(smooth.value);
  const correction = 0.34 - smoothing * 0.0026;
  displayedPlayers = state.players.map((target) => {
    const current = displayedPlayers.find((player) => player.pid === target.pid) || {...target};
    current.x += current.vx * dt;
    current.y += current.vy * dt;
    current.x += (target.x - current.x) * correction;
    current.y += (target.y - current.y) * correction;
    current.heading = approachAngle(current.heading, target.heading, correction + 0.12);
    current.vx = target.vx;
    current.vy = target.vy;
    return current;
  });
}

function world() {
  const scale = Number(zoom.value) / 100;
  ctx.translate(500, 500);
  if (rotateView.checked && state) {
    const focus = renderPlayers().find((player) => String(player.pid) === focusPlayer.value);
    if (focus) ctx.rotate(-focus.heading - Math.PI / 2);
  }
  ctx.scale(scale, scale);
  ctx.translate(-500, -500);
}

function poly(points) {
  ctx.beginPath();
  points.forEach(([x, y], index) => {
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
}

function buildMapCache() {
  const mapId = selectedMapId();
  const layout = mapLayouts[mapId] || mapLayouts.training;
  const cache = document.createElement('canvas');
  cache.width = 1000;
  cache.height = 1000;
  const c = cache.getContext('2d');

  c.fillStyle = 'rgba(50, 58, 70, 0.9)';
  c.strokeStyle = 'rgba(118, 133, 151, 0.26)';
  c.lineWidth = 4;
  for (const points of layout.polys) {
    c.beginPath();
    points.forEach(([x, y], index) => {
      if (index === 0) c.moveTo(x, y);
      else c.lineTo(x, y);
    });
    c.closePath();
    c.fill();
    c.stroke();
  }

  c.fillStyle = 'rgba(25, 29, 38, 0.72)';
  layout.cover.forEach(([x, y, w, h]) => c.fillRect(x, y, w, h));

  c.fillStyle = 'rgba(111, 151, 210, 0.5)';
  layout.zones.forEach(([x, y, w, h]) => c.fillRect(x, y, w, h));

  c.fillStyle = 'rgba(215, 67, 80, 0.68)';
  layout.sites.forEach(([x, y, w, h]) => c.fillRect(x, y, w, h));

  c.fillStyle = 'rgba(255,255,255,0.88)';
  c.font = '700 34px system-ui, sans-serif';
  layout.sites.forEach(([x, y, , , label]) => c.fillText(label, x + 30, y + 54));

  mapCache = cache;
  cachedMap = mapId;
}

function drawMap() {
  if (!mapCache || cachedMap !== selectedMapId()) buildMapCache();
  ctx.save();
  world();
  ctx.drawImage(mapCache, 0, 0);
  ctx.restore();
}

function drawObjective() {
  if (!state?.game) return;
  const layout = mapLayouts[selectedMapId()] || mapLayouts.training;
  const site = layout.sites.find((item) => item[4] === state.game.objective.site);
  if (!site) return;
  const [x, y, w, h] = site;
  const cx = x + w / 2;
  const cy = y + h / 2;
  const pulse = 0.5 + Math.sin(performance.now() / 180) * 0.5;

  ctx.save();
  world();
  ctx.strokeStyle = `rgba(255, 212, 55, ${0.38 + pulse * 0.34})`;
  ctx.lineWidth = 8;
  ctx.beginPath();
  ctx.arc(cx, cy, 72 + pulse * 18, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = 'rgba(255, 212, 55, 0.12)';
  ctx.beginPath();
  ctx.arc(cx, cy, 82, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawCone(player, size) {
  ctx.save();
  ctx.translate(player.x, player.y);
  ctx.rotate(player.heading);
  ctx.fillStyle = player.team === 'alpha' ? 'rgba(80, 145, 255, 0.22)' : 'rgba(255, 212, 55, 0.28)';
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.arc(0, 0, size * 6.2, -0.42, 0.42);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function drawWeapon(player, size) {
  if (!showWeapons.checked) return;
  ctx.save();
  ctx.translate(player.x - size * 2.3, player.y + size * 1.8);
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 5;
  ctx.lineCap = 'round';
  ctx.beginPath();
  if (player.weapon === 'knife') {
    ctx.moveTo(-18, 6); ctx.lineTo(18, -8);
  } else if (player.weapon === 'awp') {
    ctx.moveTo(-24, 0); ctx.lineTo(24, 0); ctx.moveTo(8, 0); ctx.lineTo(16, 10);
  } else {
    ctx.moveTo(-18, 2); ctx.lineTo(16, 2); ctx.lineTo(23, -4); ctx.moveTo(0, 2); ctx.lineTo(7, 12);
  }
  ctx.stroke();
  ctx.restore();
}

function drawPlayers() {
  const size = Number(playerSize.value);
  ctx.save();
  world();
  for (const player of renderPlayers()) {
    if (!showFriends.checked && player.team === 'alpha') continue;
    const key = `${player.pid}`;
    const points = history.get(key) || [];

    if (showTrails.checked && points.length > 2) {
      ctx.strokeStyle = player.color + '90';
      ctx.lineWidth = 5;
      ctx.beginPath();
      points.forEach(([x, y], index) => index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y));
      ctx.stroke();
    }

    if (shape === 'cone') drawCone(player, size);
    if (player.alive) drawWeapon(player, size);

    ctx.save();
    ctx.translate(player.x, player.y);
    ctx.rotate(player.heading);
    ctx.globalAlpha = player.alive ? 1 : 0.32;
    ctx.fillStyle = player.alive ? player.color : '#7c8294';
    ctx.strokeStyle = player.alive ? 'rgba(255,255,255,0.72)' : 'rgba(255,255,255,0.28)';
    ctx.lineWidth = 3;
    if (shape === 'triangle') {
      ctx.beginPath();
      ctx.moveTo(size * 1.25, 0);
      ctx.lineTo(-size, -size * 0.8);
      ctx.lineTo(-size * 0.55, 0);
      ctx.lineTo(-size, size * 0.8);
      ctx.closePath();
    } else if (shape === 'bar') {
      ctx.beginPath();
      ctx.roundRect(-size * 0.35, -size * 1.3, size * 0.7, size * 2.6, 5);
    } else {
      ctx.beginPath();
      ctx.arc(0, 0, size, 0, Math.PI * 2);
    }
    ctx.fill();
    ctx.stroke();
    if (player.controlled) {
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.arc(0, 0, size + 9, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (!player.alive) {
      ctx.rotate(-player.heading);
      ctx.strokeStyle = 'rgba(255,255,255,0.75)';
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(-size * 0.72, -size * 0.72);
      ctx.lineTo(size * 0.72, size * 0.72);
      ctx.moveTo(size * 0.72, -size * 0.72);
      ctx.lineTo(-size * 0.72, size * 0.72);
      ctx.stroke();
    }
    ctx.restore();
  }
  ctx.restore();
}

function drawLabels() {
  if (!showNames.checked) return;
  const size = Number(playerSize.value);
  ctx.save();
  world();
  ctx.font = `800 ${Math.max(20, size * 1.25)}px system-ui, sans-serif`;
  ctx.textBaseline = 'middle';
  ctx.shadowColor = 'rgba(0,0,0,0.85)';
  ctx.shadowBlur = 4;
  ctx.shadowOffsetY = 2;
  for (const player of renderPlayers()) {
    if (!player.alive || (!showFriends.checked && player.team === 'alpha')) continue;
    const team = player.team === 'alpha' ? 'CT' : 'T';
    ctx.fillStyle = '#fff';
    ctx.fillText(`${team} ${player.color_name}`, player.x + size + 10, player.y + 2);
    ctx.fillStyle = '#21e037';
    ctx.fillText(`HP:[${player.hp}]`, player.x + size + 118, player.y + 2);
  }
  ctx.restore();
}

function draw() {
  if (!state) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#282b37';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawMap();
  drawObjective();
  drawPlayers();
  drawLabels();
}

function frame(timestamp) {
  const dt = Math.min(0.05, lastFrame ? (timestamp - lastFrame) / 1000 : 0);
  lastFrame = timestamp;
  animatePlayers(dt);
  draw();
  requestAnimationFrame(frame);
}

function toggleSheet(sheet) {
  const shouldOpen = sheet.classList.contains('hidden');
  settingsSheet.classList.add('hidden');
  serverSheet.classList.add('hidden');
  sheet.classList.toggle('hidden', !shouldOpen);
}

document.querySelectorAll('.segmented button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.segmented button').forEach((item) => item.classList.remove('selected'));
    button.classList.add('selected');
    shape = button.dataset.shape;
    draw();
  });
});

settingsButton.addEventListener('click', () => toggleSheet(settingsSheet));
serverButton.addEventListener('click', () => toggleSheet(serverSheet));
closeServer.addEventListener('click', () => serverSheet.classList.add('hidden'));
settingsSheet.addEventListener('click', (event) => {
  if (event.target === settingsSheet) settingsSheet.classList.add('hidden');
});
serverSheet.addEventListener('click', (event) => {
  if (event.target === serverSheet) serverSheet.classList.add('hidden');
});

region.addEventListener('change', connect);
document.querySelectorAll('input[name="hz"]').forEach((control) => control.addEventListener('change', connect));
mapChoice.addEventListener('change', () => {
  if (mapChoice.value === 'auto' && state?.map?.id && mapLayouts[state.map.id]) {
    activeMap = state.map.id;
  } else if (mapChoice.value !== 'auto') {
    activeMap = mapChoice.value;
    gameCommand('map', {value: mapChoice.value});
  }
  cachedMap = '';
  draw();
});
[showNames, showFriends, showWeapons, showTrails, rotateView, showList, zoom, playerSize, smooth].forEach((control) => {
  control.addEventListener('input', () => {
    updateReadout();
    draw();
  });
});

reset.addEventListener('click', async () => {
  await fetch('/api/reset', { method: 'POST' });
  history = new Map();
  displayedPlayers = [];
  cachedMap = '';
});

pauseGame.addEventListener('click', () => gameCommand('toggle_pause'));
nextRound.addEventListener('click', () => {
  history = new Map();
  displayedPlayers = [];
  gameCommand('next_round');
});
difficulty.addEventListener('change', () => gameCommand('difficulty', {value: difficulty.value}));
controlPlayer.addEventListener('change', () => gameCommand('control', {pid: Number(controlPlayer.value)}));

window.addEventListener('keydown', (event) => {
  const map = {ArrowUp: 'up', w: 'up', W: 'up', ArrowDown: 'down', s: 'down', S: 'down', ArrowLeft: 'left', a: 'left', A: 'left', ArrowRight: 'right', d: 'right', D: 'right'};
  const dir = map[event.key];
  if (!dir) return;
  event.preventDefault();
  setInput(dir, true);
});

window.addEventListener('keyup', (event) => {
  const map = {ArrowUp: 'up', w: 'up', W: 'up', ArrowDown: 'down', s: 'down', S: 'down', ArrowLeft: 'left', a: 'left', A: 'left', ArrowRight: 'right', d: 'right', D: 'right'};
  const dir = map[event.key];
  if (!dir) return;
  event.preventDefault();
  setInput(dir, false);
});

document.querySelectorAll('.dpad button').forEach((button) => {
  const dir = button.dataset.dir;
  button.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    button.setPointerCapture(event.pointerId);
    setInput(dir, true);
  });
  button.addEventListener('pointerup', () => setInput(dir, false));
  button.addEventListener('pointercancel', () => setInput(dir, false));
  button.addEventListener('lostpointercapture', () => setInput(dir, false));
});

setInterval(updateClock, 1000);
updateClock();
connect();
requestAnimationFrame(frame);
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the authorized radar trainer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    thread = threading.Thread(target=simulation_loop, daemon=True)
    thread.start()

    server = ThreadingHTTPServer((args.host, args.port), RadarHandler)
    print(f"Radar trainer running at http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
