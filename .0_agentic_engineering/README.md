# .0_agentic_engineering

Repo-local memory layer for long-running human + agent engineering work.

## Layout

```text
.0_agentic_engineering/
  README.md
  0_rfcs/
  1_worklog/
    0_Venkat2811/
      kanban/
```

## What lives here

- `0_rfcs/`: durable engineering decisions — one RFC per solution track
- `1_worklog/0_Venkat2811/kanban/`: per-human kanban board

## Kanban file set

- `00_BACKLOG.md`: active backlog only
- `01_READY.md`: ready-to-pull cards
- `02_IN_PROGRESS.md`: active WIP
- `03_DONE.md`: implemented work
- `04_CLOSED.md`: explicitly closed / de-scoped cards
- `kanban_summary.md`: current board summary

State flow: Backlog → Ready → In Progress → Done
WIP policy: max 3 cards in In Progress

## Policy

- RFCs are repo-level and durable.
- Worklog is human-centric.
- Each human keeps their own kanban lane.
- Per-solution `worklog/` directories hold solution-specific artifacts (baselines, evidence, research).

## Do not commit

- raw chat transcripts
- secrets or tokens
- absolute local paths
