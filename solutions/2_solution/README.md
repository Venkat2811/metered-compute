# 2_solution: Service-Grade Platform

CQRS + RabbitMQ SLA routing + reservation billing (reserve/capture/release).

Compose project name: `mc-solution2` (set in `compose.yaml`).

Primary references:

- `../0_1_rfcs/RFC-0002-2-solution-service-grade-platform/`
- `worklog/kanban/BOARD.md`

## Setup, Run, Demo (Reviewer First)

1. Setup

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv sync --dev
```

2. Run

```bash
docker compose up --build -d
./scripts/wait_ready.sh
docker compose ps
```

3. See demo

```bash
source .venv/bin/activate
python ./utils/demo.py
# optional shell demo:
./utils/demo.sh
```

Optional full proof command (quality + coverage + integration/e2e/fault + scenarios + evidence capture):

```bash
make prove
# alias:
make full-check
```

## Stack

- API/Auth: FastAPI + Hydra OAuth/JWT (`src/solution2/app.py`)
- Command side: Postgres `cmd.*` + transactional outbox
- Queue/routing: RabbitMQ topic exchange + SLA lanes (`queue.realtime`, `queue.fast`, `queue.batch`)
- Query side: projector materialized view (`query.task_query_view`)
- Billing model: reservation lifecycle (`RESERVED -> CAPTURED/RELEASED`)
- Background workers: `worker`, `outbox-relay`, `projector`, `watchdog`, `webhook-worker`
- Observability: structlog + Prometheus + Grafana

Compatibility endpoints are preserved (`/task`, `/poll`, `/admin/credits`, `/hit`).

## Lay Of The Land (Code Structure)

```text
.
|-- worklog
|   `-- kanban
|-- docker
|   |-- api
|   |-- outbox_relay
|   |-- postgres
|   |-- projector
|   |-- watchdog
|   |-- webhook_worker
|   `-- worker
|-- monitoring
|   |-- grafana
|   `-- prometheus
|-- scripts
|-- src
|   `-- solution2
|-- tests
|   |-- e2e
|   |-- fault
|   |-- integration
|   `-- unit
`-- utils
```

`src/solution2` layout:

```text
src/solution2
|-- api
|-- app.py
|-- constants.py
|-- core
|-- db
|-- models
|-- observability
|-- services
|-- utils
`-- workers
```

## Scenario and Verification Commands

```bash
source .venv/bin/activate
python ./scripts/run_scenarios.py         # 13 scenarios
make loadtest                             # deterministic load/stress harness
pytest tests/unit -q
pytest tests/integration -m integration
```
