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

__version__ = "0.1.3"
__all__ = ["__version__"]
