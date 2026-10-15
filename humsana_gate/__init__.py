"""Humsana Gate: an authorization gate for autonomous agents.

The agent does the work. This checks the action before it changes anything.
"""

from .boundary import Boundary, BoundaryError, CONTRACT, CLEARED, DECISIONS, DEFAULT_BASE

__all__ = ["Boundary", "BoundaryError", "CONTRACT", "CLEARED", "DECISIONS", "DEFAULT_BASE"]
