"""Tests for the energiedaten.at refresh button."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.energiedaten.button import EnergiedatenRefreshButton


async def test_press_triggers_coordinator_refresh():
    """Pressing the button should call coordinator.async_request_refresh."""
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()

    entry = MagicMock()
    entry.entry_id = "test-entry-id"

    button = EnergiedatenRefreshButton(coordinator, entry)
    await button.async_press()

    coordinator.async_request_refresh.assert_called_once()
