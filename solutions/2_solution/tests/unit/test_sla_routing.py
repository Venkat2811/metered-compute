import pytest

from solution2.constants import RequestMode, compute_routing_key, resolve_queue


def test_compute_routing_key() -> None:
    assert (
        compute_routing_key(mode="async", tier="pro", model_class="small")
        == "tasks.async.pro.small"
    )


def test_free_async_routes_to_batch() -> None:
    assert resolve_queue(tier="free", mode=RequestMode.ASYNC, model_class="small") == "queue.batch"


def test_free_batch_routes_to_batch() -> None:
    assert resolve_queue(tier="free", mode="batch", model_class="medium") == "queue.batch"


def test_free_sync_is_rejected() -> None:
    with pytest.raises(ValueError, match="sync"):
        resolve_queue(tier="free", mode="sync", model_class="small")


def test_pro_sync_small_routes_to_fast() -> None:
    assert resolve_queue(tier="pro", mode="sync", model_class="small") == "queue.fast"


def test_pro_sync_medium_rejected() -> None:
    with pytest.raises(ValueError, match="restricted"):
        resolve_queue(tier="pro", mode="sync", model_class="medium")


def test_enterprise_batch_routes_to_fast() -> None:
    assert resolve_queue(tier="enterprise", mode="batch", model_class="large") == "queue.fast"


def test_enterprise_async_routes_to_realtime() -> None:
    assert resolve_queue(tier="enterprise", mode="async", model_class="small") == "queue.realtime"
