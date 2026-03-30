# Solution 0 Quality Policy

Date: 2026-02-15  
Scope: `solutions/0_solution`

## Objectives

- Enforce deterministic local and CI quality checks.
- Keep strict type safety in business-critical paths.
- Include baseline security checks in normal development flow.
- Prevent silent quality regression through one-command gates.

## Required Gates

Primary gate command:

```bash
./scripts/quality_gate.sh
```

Enforced checks:

1. `ruff format --check .`
2. `ruff check .`
3. `mypy --strict src tests`
4. `bandit -q -r src -x tests -s B104`
5. `pip-audit`
6. `./scripts/secrets_check.sh`
7. `./scripts/docker_lint.sh`

Coverage gate command:

```bash
./scripts/coverage_gate.sh
```

Coverage policy:

- Global floor: `75%`
- Critical module floor: `80%` for:
  - `src/solution0/app.py`
  - `src/solution0/services/billing.py`
  - `src/solution0/worker_tasks.py`
  - `src/solution0/reaper.py`

## Suppression Policy

- Suppressions are allowed only when justified in writing inside the relevant script/config.
- Current approved suppressions:
  - `bandit` rule `B104` in container context (`0.0.0.0` bind is intentional for compose networking).
  - `hadolint` rule `DL3008` for baseline-level Dockerfiles (unpinned apt package versions).
- Any new suppression must include:
  1. Reason
  2. Risk impact
  3. Compensating control

## Secret Scanning Policy

- Baseline file: `.secrets.baseline`
- Refresh command:

```bash
./scripts/secrets_check.sh --refresh
```

- Policy:
  - Baseline drift must fail the gate.
  - Hardcoded dev keys are permitted only for reproducibility and must remain documented as dev-only.
  - Production policy remains: no hardcoded secrets in runtime images.

## Dependency and Security Hygiene

- `pip-audit` must pass with no known vulnerabilities.
- Security findings must be fixed before release or explicitly risk-accepted with documented rationale.
- Latest gate evidence is recorded in `worklog/baselines/`.
