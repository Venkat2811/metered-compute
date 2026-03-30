# P1-031: Consolidated Final Hardening Pass

Priority: P1
Status: done
Depends on: P1-030

## Objective

Resolve only validated, high-signal review findings in one focused pass across security, correctness, runtime behavior, tests, and docs.

## Checklist

- [x] Add webhook SSRF guardrails (private/reserved targets blocked) at registration and dispatch paths
- [x] Remove event-loop blocking during worker warmup (no `time.sleep` on async path)
- [x] Harden JWKS client cache synchronization for concurrent verification calls
- [x] Parallelize revocation fallback checks (no sequential Redis round-trips)
- [x] Add `/v1/oauth/token` rate limiting with deterministic Redis-backed policy
- [x] Mask API keys in structured logs (admin and related auth paths)
- [x] Add explicit numeric bounds for `SubmitTaskRequest.x` and `SubmitTaskRequest.y`
- [x] Make fake Redis pipeline return execute results compatible with production semantics
- [x] Add unit coverage for `stream_worker.main_async` lifecycle
- [x] Add unit coverage for `webhook_dispatcher.main_async` lifecycle
- [x] Align `1_solution/README.md` DB-call math and related request-flow wording
- [x] Align `RFC-0001` Lua pseudo-code/args/field names with shipped implementation
- [x] Align `solutions/README.md` container/service naming with compose reality
- [x] Clarify tier-concurrency wording in `the spec baseline` vs multiplier model
- [x] Remove or deprecate stale flat RFC file `RFC-0001-1-solution-redis-native-engine.md`
- [x] Run `make prove` from clean state and record evidence path

## Acceptance Criteria

- [x] All checklist items implemented and validated
- [x] `ruff check`, `mypy --strict`, and tests remain green
- [x] `make prove` passes from clean compose state

## Evidence

- Full-check artifacts: `solutions/1_solution/worklog/evidence/full-check-20260217T064959Z`
