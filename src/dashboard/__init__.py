"""Dashboard (M3): FastAPI app hosting the sensing engine + Mapper.

The dashboard is a consumer of the sensing seam, exactly like the console
renderer — it registers a bridge with the engine and presents; it never
forks or modifies the engine.
"""

from .annotations import append_annotation, build_record
from .app import create_app
from .bridge import DashboardBridge, EnvelopeAdvisory
from .presence import PresenceGate, assess_presence

__all__ = [
    "DashboardBridge",
    "EnvelopeAdvisory",
    "PresenceGate",
    "append_annotation",
    "assess_presence",
    "build_record",
    "create_app",
]
