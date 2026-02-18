#  Assignment Repository

This repository contains:
- the original assignment input,
- a shared assumptions + RFC set,
- multiple solution tracks with different architectural tradeoffs.

Start here:
1. `solutions/README.md` (solution matrix and how tracks differ)
2. `solutions/0_0_problem_statement_and_assumptions/README.md` (baseline contract and assumptions)
3. `solutions/0_1_rfcs/` (deep architecture docs per track)

## Directory Layout (Lay of the Land)

Excluded from this map by design: `_wip/`, `.claude/`, `__temp__/`.

```text
metered-compute/
|-- .git/                                  # git metadata
|-- .ruff_cache/                           # local lint cache
|-- LICENSE
|-- README.md                              # this file
|-- original-task/
|   `-- api_playground-master/
|       `-- README.md                      # canonical assignment prompt/input
`-- solutions/
    |-- README.md                          # solution matrix + run guidance
    |-- 0_0_problem_statement_and_assumptions/
    |   `-- README.md                      # shared baseline contract + assumptions
    |-- 0_1_rfcs/
    |   |-- RFC-0000-0-solution-celery-baseline/
    |   |   `-- README.md                  # Sol 0 architecture
    |   |-- RFC-0001-1-solution-redis-native-engine/
    |   |   `-- README.md                  # Sol 1 architecture
    |   |-- RFC-0002-2-solution-service-grade-platform/
    |   |   `-- README.md                  # Sol 2 architecture
    |   |-- RFC-0003-3-solution-financial-core/
    |   |   `-- README.md                  # Sol 3 (RFC only)
    |   |-- RFC-0004-4-solution-production-launch/
    |   |   `-- README.md                  # Sol 4 (RFC only)
    |   `-- RFC-0005-5-solution-tb-restate-showcase/
    |       `-- README.md                  # Sol 5 architecture
    |-- 0_solution/
    |   `-- README.md                      # implemented: Celery + Redis + Postgres
    |-- 1_solution/
    |   `-- README.md                      # implemented: JWT + Redis Streams + Lua
    |-- 2_solution/
    |   `-- README.md                      # implemented: CQRS + RabbitMQ + reservations
    |-- 3_solution/
    |   `-- README.md                      # track stub (RFC-only implementation target)
    |-- 4_solution/
    |   `-- README.md                      # track stub (RFC-only launch blueprint)
    `-- 5_solution/
        `-- README.md                      # implemented: TigerBeetle + Restate showcase
```

## Read Order (Recommended)

1. `original-task/api_playground-master/README.md`
2. `solutions/0_0_problem_statement_and_assumptions/README.md`
3. `solutions/README.md`
4. RFC folder matching the track you are reviewing (`solutions/0_1_rfcs/...`)
5. Corresponding implementation README (`solutions/<n>_solution/README.md`)

## Note from Venkat

Development was done using Agentic Engineering - claude-code for planning, codex-cli for implementation, cursor as overall IDE

Time Spent: ~40 hours
- Opened assignment dir on Saturday
- Active and Passive planning ~6 hours
- Basic repo structure and initial rfc ~2hrs
- 0_solution ~10 hours
- 1_solution ~10 hours
- 2_solution ~8 hours
- 3_solution ~1 hour
- 4_solution ~1 hour
- 5_solution ~4 hours (in parallel with 2_solution)

All runnable solutions were verified on Macbook Pro
