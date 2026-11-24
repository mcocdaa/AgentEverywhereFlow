"""AgentEverywhereFlow (AEFlow).

Agent on Everywhere: Summon an autonomous GUI agent on any screen or window.
"""

import sys

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

__version__ = "0.1.5"

from agenteverywhereflow.config import (  # noqa: E402
    AppConfig,
    ConfigurationError,
    ExecutionMode,
    PermissionMode,
)
from agenteverywhereflow.sdk import (  # noqa: E402
    Agent,
    RunResult,
    list_targets,
    run,
    serve,
)
from agenteverywhereflow.session.session import ChatSession  # noqa: E402

__all__ = [
    "Agent",
    "AppConfig",
    "ChatSession",
    "ConfigurationError",
    "ExecutionMode",
    "PermissionMode",
    "RunResult",
    "__version__",
    "list_targets",
    "run",
    "serve",
]
