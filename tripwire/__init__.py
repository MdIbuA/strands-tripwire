"""Tripwire: a 115 ms immune system for AI agents that read untrusted content.

One #guardrails pattern with two checkpoints, each a Strands InterventionHandler:

- ProvenanceGuard   (before_tool_call)  -- who actually asked for this action?
- ContextQuarantine (after_tool_call)   -- is this chunk talking to the agent?
"""

from .decider import Decider, DeciderUnavailable
from .provenance_guard import GuardDecision, ProvenanceGuard
from .quarantine import ChunkVerdict, ContextQuarantine

__all__ = [
    "ChunkVerdict",
    "ContextQuarantine",
    "Decider",
    "DeciderUnavailable",
    "GuardDecision",
    "ProvenanceGuard",
]
