# Python 3.12 Dependency Matrix (Online Verified)

Date verified: 2026-02-15
Verification method: PyPI JSON API (`https://pypi.org/pypi/<package>/json`)

## Core Runtime Dependencies

| Package           | Selected version | PyPI latest | `requires_python` | Why selected                                                       |
| ----------------- | ---------------: | ----------: | ----------------- | ------------------------------------------------------------------ |
| fastapi           |          0.129.0 |     0.129.0 | >=3.10            | API framework                                                      |
| uvicorn[standard] |           0.40.0 |      0.40.0 | >=3.10            | ASGI server                                                        |
| celery[redis]     |            5.6.2 |       5.6.2 | >=3.9             | async worker queue                                                 |
| redis             |            6.4.0 |       7.1.1 | >=3.9             | Redis client + Lua; pinned for Celery/Kombu compatibility (`<6.5`) |
| asyncpg           |           0.31.0 |      0.31.0 | >=3.9.0           | Postgres async driver                                              |
| pydantic          |           2.12.5 |      2.12.5 | >=3.9             | request/response typing                                            |
| pydantic-settings |           2.13.0 |      2.13.0 | >=3.10            | typed config                                                       |
| structlog         |           25.5.0 |      25.5.0 | >=3.8             | structured logging                                                 |
| prometheus-client |           0.24.1 |      0.24.1 | >=3.9             | metrics export                                                     |
| orjson            |           3.11.7 |      3.11.7 | >=3.10            | fast JSON handling                                                 |
| uuid6             |         2025.0.1 |    2025.0.1 | >=3.8             | UUIDv7 generation for task IDs                                     |

## Test and Quality Dependencies

| Package           | Selected version | PyPI latest | `requires_python` | Why selected         |
| ----------------- | ---------------: | ----------: | ----------------- | -------------------- |
| pytest            |            9.0.2 |       9.0.2 | >=3.10            | test runner          |
| pytest-asyncio    |            1.3.0 |       1.3.0 | >=3.10            | async tests          |
| pytest-cov        |            7.0.0 |       7.0.0 | >=3.9             | coverage checks      |
| fakeredis         |           2.33.0 |      2.33.0 | >=3.7             | Redis/Lua unit tests |
| httpx             |           0.28.1 |      0.28.1 | >=3.8             | API test client      |
| mypy              |           1.19.1 |      1.19.1 | >=3.9             | strict type checking |
| ruff              |           0.15.1 |      0.15.1 | >=3.7             | lint + format        |
| typing-extensions |           4.15.0 |      4.15.0 | >=3.9             | typing backports     |

## Container Baseline (Online Verified)

Validated tags from Docker Hub API:

- `library/python:3.12.12-slim-bookworm`
- `library/redis:8.2.4-alpine3.22`
- `library/postgres:17.6-alpine3.22`
- `prom/prometheus:v3.5.1`
- `grafana/grafana:12.3.3`

These are reproducible defaults for local/CI compose. Production images should also pin digests.

## Verification Command (Executed)

```bash
python3 - <<'PY'
import json, urllib.request
pkgs=[
'fastapi','uvicorn','celery','redis','asyncpg','pydantic','pydantic-settings',
'structlog','prometheus-client','orjson','uuid6','httpx','pytest','pytest-asyncio',
'pytest-cov','fakeredis','mypy','ruff','typing-extensions'
]
for p in pkgs:
    data=json.load(urllib.request.urlopen(f'https://pypi.org/pypi/{p}/json'))
    print(p, data['info']['version'], data['info']['requires_python'])
PY
```
