"""
Precision / recall evaluation for the resolution layer.

CLAUDE.md requires resolution to be measured with precision/recall on a labeled
sample, not eyeballed. This is the reusable scorer; the labeled corpora live in
tests/resolution/.

`prf` compares a predicted set of items against a gold set and returns
precision, recall, F1, and the confusion counts. Items can be anything hashable
(here: (target_id, relation) tuples for the text linker).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Iterable


@dataclass
class Score:
    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int


def prf(predicted: Iterable[Hashable], gold: Iterable[Hashable]) -> Score:
    pred = set(predicted)
    gold_set = set(gold)
    tp = len(pred & gold_set)
    fp = len(pred - gold_set)
    fn = len(gold_set - pred)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return Score(round(precision, 4), round(recall, 4), round(f1, 4), tp, fp, fn)


def aggregate(scores: list[Score]) -> Score:
    """Micro-average a list of per-example scores (sum the confusion counts)."""
    tp = sum(s.true_positives for s in scores)
    fp = sum(s.false_positives for s in scores)
    fn = sum(s.false_negatives for s in scores)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return Score(round(precision, 4), round(recall, 4), round(f1, 4), tp, fp, fn)
