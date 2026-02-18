from solution2.constants import ReservationState, is_valid_reservation_transition


def test_reserved_to_captured_is_valid() -> None:
    assert is_valid_reservation_transition(
        current_state=ReservationState.RESERVED,
        next_state=ReservationState.CAPTURED,
    )


def test_reserved_to_released_is_valid() -> None:
    assert is_valid_reservation_transition(
        current_state=ReservationState.RESERVED,
        next_state=ReservationState.RELEASED,
    )


def test_captured_to_any_state_is_invalid() -> None:
    for next_state in ReservationState:
        assert (
            is_valid_reservation_transition(
                current_state=ReservationState.CAPTURED,
                next_state=next_state,
            )
            is False
        )


def test_released_to_any_state_is_invalid() -> None:
    for next_state in ReservationState:
        assert (
            is_valid_reservation_transition(
                current_state=ReservationState.RELEASED,
                next_state=next_state,
            )
            is False
        )


def test_reserved_to_reserved_is_invalid() -> None:
    assert (
        is_valid_reservation_transition(
            current_state=ReservationState.RESERVED,
            next_state=ReservationState.RESERVED,
        )
        is False
    )
