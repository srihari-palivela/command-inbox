"""System 1: the decision engine (an open-source alternative to a hosted "System One" model)."""

from __future__ import annotations

from command_inbox.agents.decision.engine import ResilientEngine, engine_from_settings, set_engine_for_tests
from command_inbox.agents.decision.heuristic import HeuristicEngine
from command_inbox.agents.decision.remote import LlamaCppEngine, VllmEngine
from command_inbox.agents.decision.types import (
    Calibration,
    ChoiceResult,
    DecisionEngine,
    DecisionEngineError,
    DecisionState,
    Option,
    ScoreResult,
)

__all__ = [
    "Calibration",
    "ChoiceResult",
    "DecisionEngine",
    "DecisionEngineError",
    "DecisionState",
    "HeuristicEngine",
    "LlamaCppEngine",
    "Option",
    "ResilientEngine",
    "ScoreResult",
    "VllmEngine",
    "engine_from_settings",
    "set_engine_for_tests",
]
