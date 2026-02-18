from __future__ import annotations

from dataclasses import dataclass
from random import Random
from uuid import UUID, uuid4

from solution2.constants import TaskStatus


@dataclass
class _TaskState:
    task_id: UUID
    cost: int
    status: TaskStatus
    refunded: bool = False


@dataclass
class _LedgerState:
    balance: int
    tasks: dict[UUID, _TaskState]

    def submit(self, cost: int, max_tasks: int) -> None:
        if len(self.tasks) >= max_tasks or self.balance < cost:
            return

        self.balance -= cost
        self.tasks[uuid4()] = _TaskState(
            task_id=UUID(int=0),
            # placeholder will be overwritten below for deterministic identity
            cost=cost,
            status=TaskStatus.PENDING,
        )

    def _first_task(self) -> _TaskState | None:
        if not self.tasks:
            return None
        # deterministic iteration for fuzz reproducibility.
        return next(iter(self.tasks.values()))

    def admit_task(self, task_id: UUID, cost: int) -> None:
        if task_id in self.tasks or self.balance < cost:
            return
        self.balance -= cost
        self.tasks[task_id] = _TaskState(task_id=task_id, cost=cost, status=TaskStatus.PENDING)

    def mark_running(self, task_id: UUID) -> None:
        task = self.tasks.get(task_id)
        if task is None or task.status != TaskStatus.PENDING:
            return
        task.status = TaskStatus.RUNNING

    def mark_completed(self, task_id: UUID) -> None:
        task = self.tasks.get(task_id)
        if task is None or task.status != TaskStatus.RUNNING:
            return
        task.status = TaskStatus.COMPLETED

    def _apply_refund(self, task_id: UUID) -> None:
        task = self.tasks[task_id]
        if task.refunded:
            return
        self.balance += task.cost
        task.refunded = True

    def apply_failure(self, task_id: UUID) -> None:
        task = self.tasks.get(task_id)
        if task is None or task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            return
        task.status = TaskStatus.FAILED
        self._apply_refund(task_id)

    def apply_cancel(self, task_id: UUID) -> None:
        task = self.tasks.get(task_id)
        if task is None or task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            return
        task.status = TaskStatus.CANCELLED
        self._apply_refund(task_id)

    def apply_stuck_recovery(self, task_id: UUID) -> None:
        task = self.tasks.get(task_id)
        if task is None or task.status != TaskStatus.RUNNING:
            return
        task.status = TaskStatus.FAILED
        self._apply_refund(task_id)

    def assert_invariants(self) -> None:
        assert self.balance >= 0

        for task in self.tasks.values():
            assert task.refunded in {False, True}

            if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                assert task.refunded is True

    def select(self, rng: Random) -> UUID | None:
        if not self.tasks:
            return None
        idx = rng.randrange(len(self.tasks))
        return list(self.tasks.keys())[idx]


def _run_deterministic_invariant_fuzz(seed: int, *, steps: int = 80) -> int:
    rng = Random(seed)
    state = _LedgerState(balance=30, tasks={})

    # Pre-seed a small number of user-visible tasks to avoid no-op phases.
    for seed_task in range(2):
        state.admit_task(task_id=uuid4(), cost=seed_task + 1)

    for _ in range(steps):
        event = rng.choice(["submit", "run", "complete", "cancel", "failure", "stuck"])

        if event == "submit":
            state.admit_task(task_id=uuid4(), cost=rng.randint(1, 7))
            state.assert_invariants()
            continue

        task_id = state.select(rng)
        if task_id is None:
            continue

        if event == "run":
            state.mark_running(task_id)
        elif event == "complete":
            state.mark_completed(task_id)
        elif event == "cancel":
            state.apply_cancel(task_id)
        elif event == "failure":
            state.apply_failure(task_id)
        elif event == "stuck":
            state.apply_stuck_recovery(task_id)

        state.assert_invariants()

    return state.balance


def test_deterministic_seed_fuzz_holds_credit_invariants() -> None:
    for seed in range(40):
        final_balance = _run_deterministic_invariant_fuzz(seed)
        assert final_balance >= 0


def test_interleaving_replays_do_not_double_refund() -> None:
    state = _LedgerState(balance=5, tasks={})
    task_id = uuid4()
    state.admit_task(task_id=task_id, cost=3)
    state.mark_running(task_id)

    # Cancellation race with late failure/reaper-style cleanup.
    state.apply_cancel(task_id)
    state.apply_failure(task_id)
    state.apply_stuck_recovery(task_id)
    state.mark_running(task_id)
    state.apply_stuck_recovery(task_id)

    assert state.balance == 5  # deducted 3, refunded 3 once.
    state.assert_invariants()
    assert len([task for task in state.tasks.values() if task.refunded]) == 1
