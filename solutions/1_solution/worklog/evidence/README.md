# Evidence Layout

- `full-check/`: timestamped outputs from `make full-check`
- `scenarios/`: scripted demo and multi-user scenario logs
- `faults/`: degradation and recovery experiment logs
- `load/`: capacity model and load test summaries

Naming recommendation:

- `full-check-YYYYMMDDTHHMMSSZ/summary.json`
- include `quality.log`, `integration.log`, `fault.log`, `e2e.log`, `compose.log`
