# .0_agentic_engineering

Repo-local memory layer for long-running human + agent engineering work.

## Layout

```text
.0_agentic_engineering/
  README.md
  0_rfcs/
    RFC-0000-0-solution-celery-baseline/
    RFC-0001-1-solution-redis-native-engine/
    RFC-0002-2-solution-service-grade-platform/
    RFC-0003-3-solution-financial-core/
    RFC-0004-4-solution-tb-restate-showcase/
  1_worklog/
    0_Venkat2811/
      kanban_summary.md          # aggregate across all solutions
      0_solution/kanban/         # Sol 0 kanban board
      1_solution/kanban/         # Sol 1 kanban board
      2_solution/kanban/         # Sol 2 kanban board
      3_solution/kanban/         # Sol 3 kanban board
      4_solution/kanban/         # Sol 4 kanban board
```

## What lives here

- `0_rfcs/`: durable engineering decisions — one RFC per solution track
- `1_worklog/0_Venkat2811/`: per-human worklog with per-solution kanban boards

## Kanban file set (per solution)

Each `<N>_solution/kanban/` directory contains:

- `BOARD.md`: overview and links for the solution
- `00_BACKLOG.md`: active backlog only
- `01_READY.md`: ready-to-pull cards
- `02_IN_PROGRESS.md`: active WIP
- `done/`: individual card files for completed work
- `closed/`: individual card files for de-scoped work (if any)
- `kanban_summary.md`: per-solution board summary

State flow: Backlog → Ready → In Progress → Done
WIP policy: max 3 cards in In Progress

## Policy

- RFCs are repo-level and durable.
- Worklog is human-centric.
- Each human keeps their own worklog lane.
- Per-solution kanban boards preserve the development narrative for each track.
- Per-solution `worklog/` directories in `solutions/` hold solution-specific artifacts (baselines, evidence, research).

## Do not commit

- raw chat transcripts
- secrets or tokens
- absolute local paths
