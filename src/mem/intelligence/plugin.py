"""
Pluggable intelligent memory plugin interface and default implementation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .importance_evaluator import ImportanceEvaluator
from .ebbinghaus_algorithm import EbbinghausAlgorithm


logger = logging.getLogger(__name__)


class IntelligentMemoryPlugin:
    """
    Interface for intelligent memory plugins.
    Implementations can annotate new memories and manage lifecycle on access/search.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}

    @property
    def enabled(self) -> bool:  # pragma: no cover - trivial
        return bool(self.config.get("enabled", False))

    def on_add(self, *, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Hook invoked before persisting a memory. Return extra fields to merge into record.
        """
        return {}

    def on_get(self, memory: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Hook invoked on single memory access.
        Returns (updates, delete_flag). If delete_flag is True, caller should delete memory.
        """
        return None, False

    def on_search(self, results: List[Dict[str, Any]]) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
        """
        Hook invoked on batch search results.
        Returns (updates, delete_ids).
        updates: list of (memory_id, update_dict)
        delete_ids: list of memory ids to delete
        """
        return [], []


class EbbinghausIntelligencePlugin(IntelligentMemoryPlugin):
    """
    Default plugin implementing importance evaluation and lifecycle with Ebbinghaus.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._importance = None
        self._algo = None
        if self.enabled:
            try:
                self._importance = ImportanceEvaluator(
                    self.config.get("importance", {}),
                    self.config.get("llm", {}),
                )
                self._algo = EbbinghausAlgorithm(self.config.get("ebbinghaus", {}))
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(f"Failed to init Ebbinghaus plugin: {e}")
                self.config["enabled"] = False

    def _classify(self, score: float) -> str:
        if not self._algo:
            return "working"
        if score >= self._algo.long_term_threshold:
            return "long_term"
        if score >= self._algo.short_term_threshold:
            return "short_term"
        return "working"

    def on_add(self, *, content: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.enabled or not self._importance or not self._algo:
            return {}
        try:
            score = self._importance.evaluate_importance(content, metadata)
            return {
                "importance_score": score,
                "memory_type": self._classify(score),
                "access_count": 0,
            }
        except Exception:
            return {"access_count": 0}

    def on_get(self, memory: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
        if not self.enabled or not self._algo:
            return None, False
        try:
            updates: Dict[str, Any] = {
                "access_count": (memory.get("access_count") or 0) + 1,
                "updated_at": datetime.utcnow(),
            }
            if self._algo.should_forget(memory):
                return None, True
            if self._algo.should_promote(memory):
                current = memory.get("memory_type")
                if current == "working":
                    updates["memory_type"] = "short_term"
                elif current == "short_term":
                    updates["memory_type"] = "long_term"
            if self._algo.should_archive(memory):
                meta = memory.get("metadata") or {}
                meta["archived"] = True
                updates["metadata"] = meta
            return updates, False
        except Exception:
            return None, False

    def on_search(self, results: List[Dict[str, Any]]) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
        if not self.enabled or not self._algo:
            return [], []
        updates: List[Tuple[str, Dict[str, Any]]] = []
        deletes: List[str] = []
        for item in results:
            try:
                mem_id = item.get("id") or item.get("memory_id")
                if not mem_id:
                    continue
                upd, delete_flag = self.on_get(item)
                if delete_flag:
                    deletes.append(mem_id)
                elif upd:
                    updates.append((mem_id, upd))
            except Exception:
                continue
        return updates, deletes


