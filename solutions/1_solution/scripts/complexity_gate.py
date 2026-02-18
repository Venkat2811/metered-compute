#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Threshold:
    max_complexity: int
    max_lines: int


DEFAULT_THRESHOLD = Threshold(max_complexity=25, max_lines=120)
OVERRIDES: dict[str, Threshold] = {
    # FastAPI route registration is currently assembled in create_app(); keep this
    # explicit until route modules are split in a later refactor.
    "src/solution1/app.py::create_app": Threshold(max_complexity=110, max_lines=700),
    # Submit orchestration is intentionally explicit in task_write_routes.
    "src/solution1/api/task_write_routes.py::register_task_write_routes": Threshold(
        max_complexity=110, max_lines=700
    ),
    "src/solution1/api/task_write_routes.py::submit_task": Threshold(
        max_complexity=55, max_lines=320
    ),
    "src/solution1/app.py::resolve_user_from_jwt_token": Threshold(
        max_complexity=45, max_lines=170
    ),
    "src/solution1/workers/stream_worker.py::main_async": Threshold(
        max_complexity=35, max_lines=120
    ),
}


def _cyclomatic_complexity(node: ast.AST) -> int:
    complexity = 1
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.Try,
                ast.ExceptHandler,
                ast.With,
                ast.AsyncWith,
                ast.IfExp,
                ast.BoolOp,
                ast.comprehension,
                ast.Match,
            ),
        ):
            complexity += 1
    return complexity


def _iter_functions(module: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(node)
    return found


def _effective_threshold(path: str, function_name: str) -> Threshold:
    key = f"{path}::{function_name}"
    return OVERRIDES.get(key, DEFAULT_THRESHOLD)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static function complexity and size gate.")
    parser.add_argument(
        "--root",
        default="src/solution1",
        help="Root package path to scan",
    )
    parser.add_argument(
        "--output",
        default="worklog/baselines/latest-complexity-gate.json",
        help="Output JSON report path",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    root = (repo_root / args.root).resolve()
    output_path = (
        Path(args.output)
        if Path(args.output).is_absolute()
        else (repo_root / args.output).resolve()
    )

    violations: list[dict[str, object]] = []
    inspected: list[dict[str, object]] = []
    for source in sorted(root.rglob("*.py")):
        relative = str(source.relative_to(repo_root))
        module = ast.parse(source.read_text())
        for fn in _iter_functions(module):
            if fn.end_lineno is None:
                continue
            threshold = _effective_threshold(relative, fn.name)
            line_count = fn.end_lineno - fn.lineno + 1
            complexity = _cyclomatic_complexity(fn)
            record = {
                "path": relative,
                "function": fn.name,
                "lines": line_count,
                "complexity": complexity,
                "threshold": {
                    "max_lines": threshold.max_lines,
                    "max_complexity": threshold.max_complexity,
                },
            }
            inspected.append(record)
            if line_count > threshold.max_lines or complexity > threshold.max_complexity:
                violations.append(record)

    report = {
        "root": str(root),
        "inspected_count": len(inspected),
        "violation_count": len(violations),
        "violations": violations,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))

    if violations:
        print(json.dumps(report, indent=2))
        return 1

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
