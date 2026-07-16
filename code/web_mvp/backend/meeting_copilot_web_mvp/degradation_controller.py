"""5-level degradation controller for Meeting Copilot.

Implements the degradation strategy defined in PRD failure-and-degradation.md.
Levels:
  0 (Normal)    - Full functionality
  1 (Light)     - Suggestion cards reduced (high confidence only)
  2 (Moderate)  - Stop real-time suggestions, continue ASR + correction
  3 (Heavy)     - Stop ASR, continue audio recording
  4 (Stop)      - Audio capture failed
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable
import structlog

logger = structlog.get_logger(__name__)

LEVEL_NORMAL = 0
LEVEL_LIGHT = 1
LEVEL_MODERATE = 2
LEVEL_HEAVY = 3
LEVEL_STOP = 4

LEVEL_LABELS = {
    0: "正常",
    1: "轻微降级",
    2: "中度降级",
    3: "重度降级",
    4: "已停止",
}


class DegradationController:
    """Controls system degradation levels.

    Escalation is sticky until an explicit recovery signal or manual reset.
    ASR sidecar failures can recover when a real recognizer reports ready;
    unrelated provider or audio failures still require their own signal.
    Observers are notified on level changes.
    """

    def __init__(self) -> None:
        self._level = LEVEL_NORMAL
        self._reason = ""
        self._observers: list[Callable[[int, str], None]] = []
        self._lock = RLock()

    @property
    def level(self) -> int:
        with self._lock:
            return self._level

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    @property
    def label(self) -> str:
        with self._lock:
            return LEVEL_LABELS.get(self._level, "未知")

    def set_level(self, level: int, reason: str = "") -> None:
        """Escalate to a higher degradation level. Does nothing if level is lower or equal."""
        with self._lock:
            if level <= self._level:
                return
            old = self._level
            self._level = level
            self._reason = reason
            logger.warning(
                "degradation_escalated",
                old_level=old,
                new_level=level,
                reason=reason,
            )
            observers = list(self._observers)
        for observer in observers:
            try:
                observer(level, reason)
            except Exception:
                logger.exception("degradation_observer_failed")

    def reset(self) -> None:
        """Manually reset to normal level."""
        with self._lock:
            old = self._level
            self._level = LEVEL_NORMAL
            self._reason = ""
            logger.info("degradation_reset", previous_level=old)
            observers = list(self._observers)
        for observer in observers:
            try:
                observer(LEVEL_NORMAL, "")
            except Exception:
                logger.exception("degradation_observer_failed")

    def recover(self, reason: str = "") -> bool:
        """Clear a recoverable ASR degradation after a real ASR ready signal.

        A healthy ASR worker must not silently clear an unrelated LLM/provider
        or audio-capture failure. Returning whether the level changed keeps
        the call explicit for stream-level diagnostics and tests.
        """
        with self._lock:
            if self._level == LEVEL_NORMAL:
                return False
            current_reason = str(self._reason or "")
            if not current_reason.startswith("asr_"):
                return False
            old = self._level
            self._level = LEVEL_NORMAL
            self._reason = ""
            logger.info(
                "degradation_recovered",
                old_level=old,
                reason=reason,
                previous_reason=current_reason,
            )
            observers = list(self._observers)
        for observer in observers:
            try:
                observer(LEVEL_NORMAL, "")
            except Exception:
                logger.exception("degradation_observer_failed")
        return True

    def add_observer(self, observer: Callable[[int, str], None]) -> None:
        with self._lock:
            self._observers.append(observer)

    def can_generate_suggestions(self) -> bool:
        with self._lock:
            return self._level <= LEVEL_LIGHT

    def can_call_llm(self) -> bool:
        with self._lock:
            return self._level <= LEVEL_LIGHT

    def can_run_asr(self) -> bool:
        with self._lock:
            return self._level <= LEVEL_MODERATE

    def can_record_audio(self) -> bool:
        with self._lock:
            return self._level <= LEVEL_HEAVY

    def suggestion_filter(self, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter suggestion cards based on degradation level."""
        with self._lock:
            if self._level >= LEVEL_MODERATE:
                return []
            if self._level == LEVEL_LIGHT:
                return [
                    c for c in cards
                    if float(c.get("confidence", 0)) >= 0.90
                ]
            return cards

    def to_status_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "level": self._level,
                "label": LEVEL_LABELS.get(self._level, "未知"),
                "reason": self._reason,
                "can_generate_suggestions": self._level <= LEVEL_LIGHT,
                "can_call_llm": self._level <= LEVEL_LIGHT,
                "can_run_asr": self._level <= LEVEL_MODERATE,
                "can_record_audio": self._level <= LEVEL_HEAVY,
            }


# Singleton instance
_degradation_controller: DegradationController | None = None
_degradation_lock = RLock()


def get_degradation_controller() -> DegradationController:
    global _degradation_controller
    with _degradation_lock:
        if _degradation_controller is None:
            _degradation_controller = DegradationController()
        return _degradation_controller
