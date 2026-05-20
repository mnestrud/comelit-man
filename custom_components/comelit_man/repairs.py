"""Repairs platform for Comelit Man."""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create a fix flow for the given issue.

    Currently handles:
      auth_failed — token invalid/expired; instructs user to re-authenticate.
    """
    if issue_id == "auth_failed":
        return ConfirmRepairFlow()
    raise ValueError(f"Unknown issue: {issue_id}")
