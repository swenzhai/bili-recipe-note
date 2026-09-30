"""Supervise the mobile library index without losing service after one failure."""

from __future__ import annotations

import asyncio
import logging
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)


class RecipeIndexSupervisor:
    def __init__(self, store: Any, *, interval: float = 10, maximum_delay: float = 300) -> None:
        if interval <= 0 or maximum_delay < interval:
            raise ValueError("索引间隔必须为正，最大退避不能小于索引间隔")
        self.store = store
        self.interval = interval
        self.maximum_delay = maximum_delay
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "status": "starting",
            "last_attempt": None,
            "last_success": None,
            "last_error": None,
            "consecutive_failures": 0,
            "next_retry_seconds": 0,
            "last_result": None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    async def run_once(self) -> float:
        with self._lock:
            self._state["last_attempt"] = datetime.now(timezone.utc).isoformat()
        try:
            result = await asyncio.to_thread(self.store.index_recipes)
            errors = result.get("errors") or []
            if errors:
                summary = "; ".join(str(item.get("error") or item) for item in errors[:3])
                raise RuntimeError(f"{len(errors)} 条菜谱读取失败，保留上次可用版本：{summary}")
        except Exception as exc:
            with self._lock:
                failures = self._state["consecutive_failures"] + 1
                delay = min(self.maximum_delay, self.interval * 2 ** min(failures - 1, 20))
                self._state.update(
                    status="degraded", last_error=f"{type(exc).__name__}: {exc}"[:2000],
                    consecutive_failures=failures, next_retry_seconds=delay,
                )
            logger.warning("Recipe index failed; retry in %s seconds: %s", delay, exc)
            return delay
        with self._lock:
            self._state.update(
                status="ok", last_success=datetime.now(timezone.utc).isoformat(), last_error=None,
                consecutive_failures=0, next_retry_seconds=self.interval, last_result=result,
            )
        return self.interval

    async def run_forever(self, *, initial_delay: float = 0) -> None:
        delay = initial_delay
        while True:
            if delay:
                await asyncio.sleep(delay)
            delay = await self.run_once()
