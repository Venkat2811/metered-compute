from solution2.constants import TaskStatus, is_valid_task_transition


def test_pending_to_running_is_valid() -> None:
    assert is_valid_task_transition(
        current_state=TaskStatus.PENDING,
        next_state=TaskStatus.RUNNING,
    )


def test_running_to_completed_is_valid() -> None:
    assert is_valid_task_transition(
        current_state=TaskStatus.RUNNING,
        next_state=TaskStatus.COMPLETED,
    )


def test_running_to_failed_is_valid() -> None:
    assert is_valid_task_transition(
        current_state=TaskStatus.RUNNING,
        next_state=TaskStatus.FAILED,
    )


def test_pending_to_cancelled_is_valid() -> None:
    assert is_valid_task_transition(
        current_state=TaskStatus.PENDING,
        next_state=TaskStatus.CANCELLED,
    )


def test_running_to_cancelled_is_valid() -> None:
    assert is_valid_task_transition(
        current_state=TaskStatus.RUNNING,
        next_state=TaskStatus.CANCELLED,
    )


def test_terminal_to_any_is_invalid() -> None:
    for terminal_state in (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ):
        for next_state in TaskStatus:
            if next_state == TaskStatus.EXPIRED:
                continue
            assert (
                is_valid_task_transition(
                    current_state=terminal_state,
                    next_state=next_state,
                )
                is False
            )


def test_pending_to_timeout_is_valid() -> None:
    assert is_valid_task_transition(
        current_state=TaskStatus.PENDING,
        next_state=TaskStatus.TIMEOUT,
    )


def test_terminal_to_expired_is_valid() -> None:
    for terminal_state in (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ):
        assert is_valid_task_transition(
            current_state=terminal_state,
            next_state=TaskStatus.EXPIRED,
        )
