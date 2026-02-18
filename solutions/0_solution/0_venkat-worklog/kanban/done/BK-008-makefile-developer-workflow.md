# BK-008: Makefile Developer Workflow

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Provide a clean Makefile-based workflow so local development, quality checks, test suites, and compose operations are consistent and reproducible.

## Checklist

- [x] Add `Makefile` with clear target groups:
  - [x] environment (`venv`, `sync`)
  - [x] quality (`fmt`, `lint`, `type`)
  - [x] tests (`test-unit`, `test-integration`, `test-e2e`, `test-fault`, `test-all`)
  - [x] runtime (`up`, `down`, `logs`, `ps`, `demo`)
  - [x] release (`gate-unit`, `gate-integration`, `gate-fault`)
- [x] Ensure targets use existing scripts where possible
- [x] Add `make help` output with brief target descriptions
- [x] Update README with canonical `make` commands

## Exit Criteria

- [x] New engineer can run full lifecycle from `make` targets only
- [x] Targets are deterministic and CI-compatible
- [x] Command surface is minimal and non-duplicative

## Evidence

- Makefile: `../../../../Makefile`
- Updated usage docs: `../../../../README.md`
- Canonical gate commands:
  - `make quality`
  - `make coverage`
  - `make gate-unit`
  - `make gate-integration`
  - `make gate-fault`
