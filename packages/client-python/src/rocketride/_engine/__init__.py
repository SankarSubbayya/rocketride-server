"""
Internal engine management subpackage.

Handles engine binary download, process lifecycle, state tracking,
and the auto-spawn logic used by RocketRideClient when no URI is provided.
"""

from .manager import EngineManager
from ..core.exceptions import UnsupportedPlatformError, EngineError, EngineNotFoundError

__all__ = [
    'EngineManager',
    'UnsupportedPlatformError',
    'EngineError',
    'EngineNotFoundError',
]
