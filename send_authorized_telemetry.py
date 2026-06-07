#!/usr/bin/env python3
"""Send authorized radar telemetry JSON to the local or deployed backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib import request


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish authorized telemetry to the radar backend.")
    parser.add_argument("file", help="Path to a telemetry JSON payload.")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/telemetry")
    parser.add_argument("--token", default="", help="RADAR_TELEMETRY_TOKEN value, if enabled on the server.")
    args = parser.parse_args()

    body = Path(args.file).read_bytes()
    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    req = request.Request(args.url, data=body, headers=headers, method="POST")
    with request.urlopen(req, timeout=10) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
