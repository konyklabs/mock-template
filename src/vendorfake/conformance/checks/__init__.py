"""Every contract, imported so that importing the suite registers all of them. Modules are grouped by subsystem, not by id; :data:`CHECKS` is sorted into id order below so a report reads C01 upward regardless of import order."""

from __future__ import annotations

from vendorfake.conformance.registry import CHECKS

from . import auth, capabilities, chaos, control_plane, discovery, errors, state, transport, webhooks

CHECKS.sort(key=lambda spec: spec.id)

__all__ = ["auth", "capabilities", "chaos", "control_plane", "discovery", "errors", "state", "transport", "webhooks"]
