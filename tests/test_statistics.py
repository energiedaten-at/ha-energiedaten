"""Tests for hourly statistics computation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.energiedaten.coordinator import _build_hourly_statistics


def test_single_hour_four_readings():
    """Four quarter-hour readings in one hour produce one statistic."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.1},
        {"timestamp": "2026-03-15T14:15:00+00:00", "value": 0.2},
        {"timestamp": "2026-03-15T14:30:00+00:00", "value": 0.3},
        {"timestamp": "2026-03-15T14:45:00+00:00", "value": 0.4},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=0.0)

    assert len(stats) == 1
    assert stats[0]["start"] == datetime(2026, 3, 15, 14, tzinfo=timezone.utc)
    assert stats[0]["state"] == pytest.approx(1.0)  # 0.1+0.2+0.3+0.4
    assert stats[0]["sum"] == pytest.approx(1.0)     # 0 + 1.0


def test_two_hours():
    """Readings spanning two hours produce two statistics with running sum."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.25},
        {"timestamp": "2026-03-15T14:15:00+00:00", "value": 0.25},
        {"timestamp": "2026-03-15T14:30:00+00:00", "value": 0.25},
        {"timestamp": "2026-03-15T14:45:00+00:00", "value": 0.25},
        {"timestamp": "2026-03-15T15:00:00+00:00", "value": 0.5},
        {"timestamp": "2026-03-15T15:15:00+00:00", "value": 0.5},
        {"timestamp": "2026-03-15T15:30:00+00:00", "value": 0.5},
        {"timestamp": "2026-03-15T15:45:00+00:00", "value": 0.5},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=0.0)

    assert len(stats) == 2
    assert stats[0]["state"] == pytest.approx(1.0)
    assert stats[0]["sum"] == pytest.approx(1.0)
    assert stats[1]["state"] == pytest.approx(2.0)
    assert stats[1]["sum"] == pytest.approx(3.0)  # 1.0 + 2.0


def test_anchor_sum_offsets_cumulative():
    """Non-zero anchor sum should offset all cumulative sums."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.5},
        {"timestamp": "2026-03-15T14:15:00+00:00", "value": 0.5},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=100.0)

    assert len(stats) == 1
    assert stats[0]["state"] == pytest.approx(1.0)
    assert stats[0]["sum"] == pytest.approx(101.0)  # 100 + 1.0


def test_empty_readings():
    """Empty readings list should return empty statistics."""
    stats = _build_hourly_statistics([], anchor_sum=0.0)
    assert stats == []


def test_partial_hour():
    """Incomplete hour (fewer than 4 readings) should still produce a statistic."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.3},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=5.0)

    assert len(stats) == 1
    assert stats[0]["state"] == pytest.approx(0.3)
    assert stats[0]["sum"] == pytest.approx(5.3)
