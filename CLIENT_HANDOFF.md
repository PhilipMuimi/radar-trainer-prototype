# 客户交付说明 / Client Handoff

## 项目状态 / Project Status

本项目已经整理为“授权实时数据雷达系统”。前端负责手机、平板和桌面雷达显示；后端负责接收授权坐标数据、地图识别、状态推送和健康检查。

This project is now structured as an authorized real-time radar system. The frontend displays the radar on mobile, tablet, and desktop; the backend receives authorized coordinate data, identifies the map, streams state, and exposes health checks.

## 工作方式 / How It Works

1. 授权数据源把玩家坐标发送到 `/api/telemetry`。
2. 后端验证 `RADAR_TELEMETRY_TOKEN`。
3. 后端保存最新帧，并通过 `/events` 实时推送给浏览器。
4. 浏览器自动更新地图、玩家颜色、血量、事件和状态。
5. 如果启用 `RADAR_REQUIRE_TELEMETRY=1`，系统不会生成假玩家；没有实时数据时会显示等待状态。

1. An authorized data source sends player coordinates to `/api/telemetry`.
2. The backend validates `RADAR_TELEMETRY_TOKEN`.
3. The backend stores the latest frame and streams it to browsers through `/events`.
4. The browser updates the map, player colors, HP, events, and status automatically.
5. With `RADAR_REQUIRE_TELEMETRY=1`, fake fallback players are disabled; the system waits for live data.

## 真实数据说明 / Real Data Note

系统已经支持真实数据接口，但真实坐标必须来自授权来源，例如自有服务器、训练服插件、回放解析器、赛事后台或客户允许接入的数据源。

The system already supports a real-data interface, but coordinates must come from an authorized source such as a owned server, training server plugin, replay parser, event backend, or another client-approved feed.

## 演示方式 / Demo Mode

为了在没有客户私有数据源的情况下完成展示，项目包含 `authorized_telemetry_replay.json` 和 `stream_authorized_telemetry.py`。它们模拟“已授权数据源”向后端发送录制帧，用于证明实时链路可用。

To demonstrate without the client's private data source, the project includes `authorized_telemetry_replay.json` and `stream_authorized_telemetry.py`. They simulate an authorized source sending recorded frames into the backend, proving the real-time pipeline works.

## 生产启动 / Production Start

```bash
export RADAR_TELEMETRY_TOKEN='change-this-long-secret'
export RADAR_REQUIRE_TELEMETRY=1
export RADAR_ALLOWED_ORIGIN='https://your-domain.example'
python3 app.py --host 0.0.0.0 --port 8000
```

## 本地验证 / Local Verification

```bash
export RADAR_TELEMETRY_TOKEN='test-secret'
export RADAR_REQUIRE_TELEMETRY=1
python3 app.py --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
python3 stream_authorized_telemetry.py --token test-secret --loop
```

For smoother visible movement:

```bash
python3 stream_authorized_telemetry.py --token test-secret --loop --hz 20 --steps 10
```

Keep the streamer running during the demo. If it is stopped, the radar keeps the last frame and marks the feed as `过期 / Stale`.

Open:

```text
http://127.0.0.1:8000
```

## 交付边界 / Delivery Boundary

本项目不绕过反作弊、不隐藏进程、不读取受保护游戏内存、不提取未授权的实时敌方坐标。

This project does not bypass anti-cheat, hide processes, read protected game memory, or extract unauthorized live enemy coordinates.
