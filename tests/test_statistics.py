"""Tests for hourly statistics computation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.energiedaten.coordinator import (
    _build_hourly_statistics,
    _statistic_id,
    _warn_on_suffix_collisions,
)


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


def test_statistic_id_names_feed_in_community_share():
    """X.01 resolves to a named statistic ID, never the slugified fallback.

    Home Assistant keys long-term statistics by ID and cannot rename them, so a
    reading that lands under the `_x_01` fallback strands its history in the
    recorder for good. Pin the name to keep that from regressing.
    """
    assert (
        _statistic_id("AT0010000000000000000000000123456", "1-1:2.9.0 X.01")
        == "energiedaten:at0010000000000000000000000123456_ec_feed_in_share"
    )


def test_suffix_collision_warns(caplog):
    """Two codes sharing a suffix are reported, naming both codes."""
    _warn_on_suffix_collisions(
        "meter-1", ["1-1:1.9.0 G.01T", "1-1:2.9.0 G.01T"]
    )
    assert "1-1:1.9.0 G.01T" in caplog.text
    assert "1-1:2.9.0 G.01T" in caplog.text
    assert "G.01T" in caplog.text


def test_current_api_vocabulary_does_not_warn(caplog):
    """The codes the platform actually emits per meter stay silent.

    Each meter direction carries distinct suffixes today, so the guard must not
    cry wolf on a normal install — otherwise the warning gets tuned out before
    a real collision ever arrives.
    """
    consumption = ["1-1:1.9.0 P.01", "1-1:1.9.0 G.01", "1-1:2.9.0 G.03"]
    feed_in = ["1-1:2.9.0 P.01", "1-1:2.9.0 G.01", "1-1:2.9.0 X.01"]
    gas = ["7-1:1.9.0 P.01"]

    for codes in (consumption, feed_in, gas):
        _warn_on_suffix_collisions("meter-1", codes)

    assert caplog.text == ""
