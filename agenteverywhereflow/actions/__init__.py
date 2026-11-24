"""OS input driver, viewport coordinate projection, and pluggable action backends."""

from agenteverywhereflow.actions.backend import ActionBackend, OSGuiBackend
from agenteverywhereflow.actions.cdp_backend import CDPActionBackend, discover_cdp_targets
from agenteverywhereflow.actions.coords import CoordinateProjector
from agenteverywhereflow.actions.driver import InputDriver, driver

__all__ = [
    "ActionBackend",
    "CDPActionBackend",
    "CoordinateProjector",
    "InputDriver",
    "OSGuiBackend",
    "discover_cdp_targets",
    "driver",
]
