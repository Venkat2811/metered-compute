from solution2.constants import ModelClass, task_cost_for_model


def test_small_model_cost_uses_base_cost() -> None:
    assert task_cost_for_model(base_cost=100, model_class=ModelClass.SMALL) == 100


def test_medium_model_cost_doubles_base() -> None:
    assert task_cost_for_model(base_cost=100, model_class=ModelClass.MEDIUM) == 200


def test_large_model_cost_quintuples_base() -> None:
    assert task_cost_for_model(base_cost=50, model_class=ModelClass.LARGE) == 250


def test_default_model_class_is_explicit_and_small() -> None:
    assert task_cost_for_model(base_cost=7, model_class=ModelClass.SMALL) == 7
