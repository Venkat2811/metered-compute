# Python 3.12 Dependency Matrix (Online Verified)

Date verified: 2026-02-16
Verification method: PyPI JSON API (`https://pypi.org/pypi/<package>/json`) using `curl + jq`

## Core Runtime Dependencies

| Package           | Selected version | PyPI latest | `requires_python` | Why selected                                     |
| ----------------- | ---------------: | ----------: | ----------------- | ------------------------------------------------ |
| fastapi           |          0.129.0 |     0.129.0 | >=3.10            | API and OAuth HTTP services                      |
| uvicorn[standard] |           0.40.0 |      0.40.0 | >=3.10            | ASGI server                                      |
| redis             |            7.1.1 |       7.1.1 | >=3.10            | Async Redis client, Lua scripts, Streams APIs    |
| asyncpg           |           0.31.0 |      0.31.0 | >=3.9.0           | Postgres driver for control-plane and reconciler |
| pydantic          |           2.12.5 |      2.12.5 | >=3.9             | typed request/response models                    |
| pydantic-settings |           2.13.0 |      2.13.0 | >=3.10            | typed settings and env management                |
| structlog         |           25.5.0 |      25.5.0 | >=3.8             | structured JSON logging                          |
| prometheus-client |           0.24.1 |      0.24.1 | >=3.9             | metrics export                                   |
| orjson            |           3.11.7 |      3.11.7 | >=3.10            | high-performance JSON encode/decode              |
| uuid6             |         2025.0.1 |    2025.0.1 | >=3.9             | UUIDv7 generation (`uuid7()`)                    |
| PyJWT             |           2.11.0 |      2.11.0 | >=3.9             | JWT issue/verify in OAuth + API services         |
| cryptography      |           46.0.5 |      46.0.5 | >=3.8             | key handling and JWT signing backend             |
| argon2-cffi       |           25.1.0 |      25.1.0 | >=3.8             | optional client-secret hashing path              |

Notes:

- `redis==7.1.1` selected to use current async Streams APIs (`xreadgroup`, `xautoclaim`) without legacy constraints from Celery compatibility.
- `passlib` remains optional for compatibility migration but not required on hot paths.

## Test and Quality Dependencies

| Package           | Selected version | PyPI latest | `requires_python` | Why selected                 |
| ----------------- | ---------------: | ----------: | ----------------- | ---------------------------- |
| pytest            |            9.0.2 |       9.0.2 | >=3.10            | test runner                  |
| pytest-asyncio    |            1.3.0 |       1.3.0 | >=3.10            | async test execution         |
| pytest-cov        |            7.0.0 |       7.0.0 | >=3.9             | coverage gates               |
| fakeredis         |           2.33.0 |      2.33.0 | >=3.7             | Redis/Lua/Streams unit tests |
| httpx             |           0.28.1 |      0.28.1 | >=3.8             | API test client              |
| mypy              |           1.19.1 |      1.19.1 | >=3.9             | strict typing gate           |
| ruff              |           0.15.1 |      0.15.1 | >=3.7             | lint + format                |
| typing-extensions |           4.15.0 |      4.15.0 | >=3.9             | typing backports             |

## Verification Command (Executed)

```bash
for p in \
  fastapi uvicorn redis asyncpg pydantic pydantic-settings structlog prometheus-client \
  orjson uuid6 pyjwt cryptography passlib argon2-cffi pytest pytest-asyncio pytest-cov \
  fakeredis httpx mypy ruff typing-extensions; do
  v=$(curl -fsSL https://pypi.org/pypi/$p/json | jq -r '.info.version')
  rp=$(curl -fsSL https://pypi.org/pypi/$p/json | jq -r '.info.requires_python')
  printf '%s\t%s\t%s\n' "$p" "$v" "$rp"
done
```
