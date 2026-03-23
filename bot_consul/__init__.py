"""
Оркестратор бота-консула.

Использование:
    from bot_consul.orchestrator import Orchestrator, OrchestratorResult, run_turn

Так тесты могут импортировать только ``bot_consul.guardrails`` без Qdrant и pydantic.
"""

__all__ = [
    "Orchestrator",
    "OrchestratorResult",
    "run_turn",
]


def __getattr__(name: str):
    if name in ("Orchestrator", "OrchestratorResult", "run_turn"):
        from bot_consul import orchestrator as _orch

        if name == "Orchestrator":
            return _orch.Orchestrator
        if name == "OrchestratorResult":
            return _orch.OrchestratorResult
        if name == "run_turn":
            return _orch.run_turn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
