# BK-006: Code Quality Standard and Lint Stack

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Standardize a production-grade code quality stack (Carbon-compatible if adopted, otherwise equivalent industry-standard tooling) with clear quality policy and automation.

## Checklist

- [x] Define quality policy document (style, safety, complexity, security, dependency hygiene)
- [x] Evaluate Carbon compatibility and decide final stack
- [x] Add/verify linters and analyzers:
  - [x] `ruff` (lint/format)
  - [x] `mypy --strict`
  - [x] security lint (`bandit` or equivalent)
  - [x] dependency audit (`pip-audit` or equivalent)
  - [x] secret scanning (`detect-secrets` or equivalent)
  - [x] Dockerfile lint (`hadolint` or equivalent)
- [x] Integrate all checks into a single quality gate command
- [x] Document false-positive handling and suppression policy

## Exit Criteria

- [x] Quality gate is deterministic and one-command runnable
- [x] Security and dependency checks are part of normal development flow
- [x] Team has a documented and enforceable quality standard

## Evidence

- Quality policy: `../../research/2026-02-15-quality-policy.md`
- One-command gate: `./scripts/quality_gate.sh`
- Tooling in project config: `../../../../pyproject.toml`
- Secret baseline and drift check:
  - `../../../../.secrets.baseline`
  - `../../../../scripts/secrets_check.sh`
