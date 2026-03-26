from __future__ import annotations

from solution3.workers._bootstrap_worker import run_worker


def main() -> None:
    run_worker(name="solution3_projector")


if __name__ == "__main__":
    main()
