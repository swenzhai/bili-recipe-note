from __future__ import annotations

import asyncio

import pytest

from bili_recipe_notes.indexing_service import RecipeIndexSupervisor


class _Store:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)

    def index_recipes(self):
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_index_failure_preserves_success_timestamp_and_recovers_with_capped_backoff() -> None:
    store = _Store([{"indexed": 2}, *[OSError("disk unavailable")] * 5, {"indexed": 3}])
    supervisor = RecipeIndexSupervisor(store, interval=10, maximum_delay=30)

    async def run():
        assert await supervisor.run_once() == 10
        last_success = supervisor.snapshot()["last_success"]
        for count, delay in enumerate([10, 20, 30, 30, 30], 1):
            assert await supervisor.run_once() == delay
            state = supervisor.snapshot()
            assert state["status"] == "degraded"
            assert state["consecutive_failures"] == count
            assert state["last_success"] == last_success
            assert "disk unavailable" in state["last_error"]
        assert await supervisor.run_once() == 10
        state = supervisor.snapshot()
        assert state["status"] == "ok"
        assert state["consecutive_failures"] == 0
        assert state["last_error"] is None
        assert state["last_result"] == {"indexed": 3}

    asyncio.run(run())


def test_partial_index_failure_is_visible_and_does_not_claim_full_success() -> None:
    supervisor = RecipeIndexSupervisor(_Store([{"indexed": 1, "errors": [{"error": "bad recipe"}]}]))
    asyncio.run(supervisor.run_once())
    state = supervisor.snapshot()
    assert state["status"] == "degraded"
    assert state["last_success"] is None
    assert "bad recipe" in state["last_error"]


def test_supervisor_loop_survives_failure_and_cancels_cleanly(monkeypatch) -> None:
    supervisor = RecipeIndexSupervisor(_Store([OSError("temporary"), {"indexed": 1}]), interval=2)
    delays = []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr("bili_recipe_notes.indexing_service.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(supervisor.run_forever(initial_delay=1))
    assert delays == [1, 2, 2]
    assert supervisor.snapshot()["status"] == "ok"
