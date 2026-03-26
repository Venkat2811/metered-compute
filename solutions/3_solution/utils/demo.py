#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Solution 3 bootstrap smoke demo.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    return parser.parse_args()


def _require_ok(response: httpx.Response, *, name: str) -> dict[str, Any]:
    payload: dict[str, Any] = response.json()
    print(f"{name}: {json.dumps(payload, separators=(',', ':'))}")
    if response.status_code != 200:
        raise RuntimeError(f"{name} returned {response.status_code}: {payload}")
    return payload


def main() -> int:
    args = _parse_args()
    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        health = _require_ok(client.get("/health"), name="health")
        ready = _require_ok(client.get("/ready"), name="ready")

    if health.get("status") != "ok":
        print(f"unexpected health payload: {health}", file=sys.stderr)
        return 1
    if ready.get("ready") is not True:
        print(f"unexpected ready payload: {ready}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
