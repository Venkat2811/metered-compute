# P2-024: Review reconciliation - docs and runbook parity

Priority: P2
Status: done

## Objective

Apply only the valid parts of the latest Sol 2 review by reconciling docs/runbook claims with current implementation and keeping solution-level commands consistent.

## Checklist

- [x] Update shared matrix wording for scenario counts to avoid stale hardcoded value.
- [x] Add Sol 2 `make loadtest` alias and update matrix capability row accordingly.
- [x] Update Sol 2 README verification section with explicit scenario count and loadtest command.
- [x] Update RFC-0002 observability wording:
  - [x] clarify alert rules file vs Alertmanager deployment
  - [x] mark OpenSearch as planned (not in compose for Sol 2)
- [x] Run verification (`make gate-unit` and `make prove`).
