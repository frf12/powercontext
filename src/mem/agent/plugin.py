"""
Pluggable Agent Orchestration plugin interface.

This allows core Memory/AsyncMemory to optionally route calls through an agent
manager (multi-agent, multi-user, hybrid) only when enabled by config.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple


logger = logging.getLogger(__name__)


class AgentPlugin:
    """Interface for agent-aware routing hooks."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}

    @property
    def enabled(self) -> bool:  # pragma: no cover - trivial
        return bool(self.config.get("enabled", False))

    # Hook signatures return possibly modified arguments
    def before_add(
        self,
        *,
        user_id: Optional[str],
        agent_id: Optional[str],
        run_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
        filters: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        return user_id, agent_id, run_id, metadata, filters

    def before_search(
        self,
        *,
        user_id: Optional[str],
        agent_id: Optional[str],
        run_id: Optional[str],
        filters: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        return user_id, agent_id, run_id, filters

    def before_get(
        self,
        *,
        user_id: Optional[str],
        agent_id: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        return user_id, agent_id

    def before_update(
        self,
        *,
        user_id: Optional[str],
        agent_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        return user_id, agent_id, metadata

    def before_delete(
        self,
        *,
        user_id: Optional[str],
        agent_id: Optional[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        return user_id, agent_id


class FactoryBackedAgentPlugin(AgentPlugin):
    """
    Optional adapter that tries to leverage the agent MemoryFactory (if available).
    If imports fail, it silently falls back to no-op behavior.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._manager = None
        if not self.enabled:
            return
        try:
            # Lazy import to avoid hard dependency
            from mem.agent.factories.memory_factory import MemoryFactory  # type: ignore
            # In real integration, you'd pass a structured config here
            # Using raw dict config for now
            # Expecting config like { enabled: True, mode: "multi_user" | "multi_agent" | "hybrid" }
            mode = self.config.get("mode")
            if mode and hasattr(MemoryFactory, "create_manager"):
                # MemoryFactory here may expect a typed config; best-effort fallback to dict
                try:
                    self._manager = MemoryFactory.create_manager(mode, self.config)  # type: ignore
                except Exception:
                    self._manager = None
        except Exception as e:
            logger.debug(f"Agent factory unavailable: {e}")
            self._manager = None

    # Example: forward hooks to manager if it defines policies; otherwise passthrough
    # These can be extended to call into manager methods to decide routing/scopes.


