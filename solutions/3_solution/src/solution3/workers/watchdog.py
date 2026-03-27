from __future__ import annotations

from solution3.core.settings import load_settings
from solution3.workers._bootstrap_worker import run_worker


def main() -> None:
    settings = load_settings()
    run_worker(name="solution3_watchdog", metrics_port=settings.watchdog_metrics_port)


if __name__ == "__main__":
    main()
