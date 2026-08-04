"""Tests for hourly statistics computation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.energiedaten.coordinator import _build_hourly_statistics


def test_two_hours():
    """Quarter-hour readings group into hourly buckets with a running sum."""
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
    assert stats[0]["start"] == datetime(2026, 3, 15, 14, tzinfo=timezone.utc)
    assert stats[0]["state"] == pytest.approx(1.0)
    assert stats[0]["sum"] == pytest.approx(1.0)
    assert stats[1]["start"] == datetime(2026, 3, 15, 15, tzinfo=timezone.utc)
    assert stats[1]["state"] == pytest.approx(2.0)
    assert stats[1]["sum"] == pytest.approx(3.0)  # 1.0 + 2.0


def test_empty_readings():
    """Empty readings list should return empty statistics."""
    stats = _build_hourly_statistics([], anchor_sum=0.0)
    assert stats == []


def test_partial_hour_accumulates_onto_anchor():
    """An incomplete hour still emits a statistic, offset by the anchor sum."""
    readings = [
        {"timestamp": "2026-03-15T14:00:00+00:00", "value": 0.3},
    ]
    stats = _build_hourly_statistics(readings, anchor_sum=5.0)

    assert len(stats) == 1
    assert stats[0]["state"] == pytest.approx(0.3)
    assert stats[0]["sum"] == pytest.approx(5.3)
