from __future__ import annotations

import argparse
import asyncio
import signal

from prometheus_client import start_http_server

from solution3.utils.logging import configure_logging, get_logger

logger = get_logger("solution3.worker")


def _parse_interval() -> float:
    parser = argparse.ArgumentParser(description="solution3 no-op worker bootstrap")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    return max(float(args.interval), 1.0)


async def _main_async(name: str, interval_seconds: float) -> None:
    stopped = asyncio.Event()

    def _stop(_: int, __: object) -> None:
        stopped.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop, sig, None)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_args: stopped.set())

    logger.info("bootstrap_worker_started", worker=name)
    while not stopped.is_set():
        logger.info("bootstrap_worker_heartbeat", worker=name)
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue

    logger.info("bootstrap_worker_stopped", worker=name)


def run_worker(
    name: str, interval_seconds: float | None = None, metrics_port: int | None = None
) -> None:
    if interval_seconds is None:
        interval_seconds = _parse_interval()

    configure_logging(enable_sensitive=False)
    if metrics_port is not None:
        start_http_server(metrics_port)
    asyncio.run(_main_async(name=name, interval_seconds=interval_seconds))
