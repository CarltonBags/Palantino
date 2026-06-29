"""Unit tests for the precision/recall scorer."""

from resolution.evaluation import aggregate, prf


def test_perfect_match() -> None:
    s = prf({"a", "b"}, {"a", "b"})
    assert s.precision == 1.0 and s.recall == 1.0 and s.f1 == 1.0


def test_all_wrong() -> None:
    s = prf({"x"}, {"a"})
    assert s.precision == 0.0 and s.recall == 0.0
    assert s.false_positives == 1 and s.false_negatives == 1


def test_partial() -> None:
    s = prf({"a", "x"}, {"a", "b"})
    assert s.true_positives == 1 and s.false_positives == 1 and s.false_negatives == 1
    assert s.precision == 0.5 and s.recall == 0.5


def test_empty_prediction_and_gold_is_perfect() -> None:
    # A snippet that should match nothing and matches nothing = correct.
    s = prf(set(), set())
    assert s.precision == 1.0 and s.recall == 1.0


def test_false_positive_on_empty_gold() -> None:
    s = prf({"a"}, set())
    assert s.precision == 0.0 and s.false_positives == 1


def test_aggregate_micro_average() -> None:
    a = prf({"a"}, {"a"})          # tp=1
    b = prf({"x"}, {"y"})          # fp=1, fn=1
    micro = aggregate([a, b])
    assert micro.true_positives == 1
    assert micro.false_positives == 1
    assert micro.false_negatives == 1
    assert micro.precision == 0.5 and micro.recall == 0.5
