"""Every contract, imported so that importing the suite registers all of them.

The modules are grouped by subsystem rather than by id, because a reader
looking for "what does the suite say about capabilities" wants one file. The
registry is sorted into id order at the end of this module so that a report
reads C01 downward whatever order the imports happen to run in -- report order
is for people, and registration order is an implementation detail nobody
should have to think about when adding a check.
"""

from __future__ import annotations

from vendorfake.conformance.registry import CHECKS

from . import capabilities, chaos, control_plane, errors, state, transport, webhooks

CHECKS.sort(key=lambda spec: spec.id)

__all__ = ["capabilities", "chaos", "control_plane", "errors", "state", "transport", "webhooks"]
