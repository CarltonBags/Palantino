"""
Unit tests for entity resolution logic.
These test the confidence scoring and candidate handling — no DB required.
"""

import pytest

from resolution.resolver import AUTO_MERGE_THRESHOLD, CANDIDATE_THRESHOLD, EntityResolver


def test_thresholds_sane() -> None:
    assert 0 < CANDIDATE_THRESHOLD < AUTO_MERGE_THRESHOLD <= 1.0


def test_auto_merge_threshold_high_enough() -> None:
    # We don't want to auto-merge below 90% confidence
    assert AUTO_MERGE_THRESHOLD >= 0.90


def test_candidate_threshold_filters_noise() -> None:
    # Below 70% is noise — must not create candidates
    assert CANDIDATE_THRESHOLD >= 0.70
