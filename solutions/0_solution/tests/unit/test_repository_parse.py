from __future__ import annotations

from solution0.db.repository import _parse_task_result


def test_parse_task_result_handles_dict_value() -> None:
    result = _parse_task_result({"z": 42})
    assert result == {"z": 42}


def test_parse_task_result_handles_json_string_value() -> None:
    result = _parse_task_result('{"z":42}')
    assert result == {"z": 42}


def test_parse_task_result_returns_none_for_non_mapping_payload() -> None:
    assert _parse_task_result("[1,2,3]") is None
    assert _parse_task_result(123) is None
