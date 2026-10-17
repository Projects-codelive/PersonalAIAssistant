"""Blocking agent work runs off the server loop; MongoDB and websockets stay on their own loops."""
import asyncio
import threading
import time

import pytest

import app.db.mongo as mongo
from app.agent.agent_worker import AgentWorker
from app.device.device_bridge import DeviceBridge


async def max_loop_stall(awaitable_factory):
    """Run a task while ticking the current loop; return the longest gap between ticks (seconds)"""
    gaps, done = [], asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    await asyncio.sleep(0.03)
    await awaitable_factory()
    done.set()
    await tick
    return max(gaps)


async def blocking_task():
    time.sleep(0.5)  # stands in for ADB taps + time.sleep pauses inside an async agent handler
    return threading.current_thread().name


async def test_blocking_work_on_server_loop_stalls_it():
    assert await max_loop_stall(blocking_task) >= 0.45  # the old behaviour, for reference


async def test_agent_worker_keeps_server_loop_responsive():
    worker = AgentWorker()
    assert await max_loop_stall(lambda: worker.run(blocking_task)) < 0.2


async def test_agent_worker_runs_on_dedicated_thread_and_returns_value():
    worker = AgentWorker()
    assert await worker.run(blocking_task) == "agent-worker"


async def test_agent_worker_propagates_exceptions():
    async def boom():
        raise ValueError("bad")
    with pytest.raises(ValueError, match="bad"):
        await AgentWorker().run(boom)


async def test_agent_worker_serializes_tasks():
    worker, running, overlaps = AgentWorker(), [0], []

    async def task():
        running[0] += 1
        overlaps.append(running[0])
        time.sleep(0.05)
        running[0] -= 1

    await asyncio.gather(*(worker.run(task) for _ in range(4)))
    assert max(overlaps) == 1


async def test_bridge_sends_on_websocket_loop_when_called_from_worker():
    bridge, sent_on = DeviceBridge(), []

    class FakeWS:
        async def accept(self):
            pass

        async def send_json(self, event):
            sent_on.append(asyncio.get_running_loop())

    await bridge.connect("phone", FakeWS())
    server_loop = asyncio.get_running_loop()
    result = await AgentWorker().run(bridge.broadcast_action, "TRIGGER_SMS", {"x": 1})
    assert result["recipients"] == 1
    assert sent_on == [server_loop]


async def test_bridge_counts_only_delivered_events():
    bridge = DeviceBridge()

    class BrokenWS:
        async def accept(self):
            pass

        async def send_json(self, event):
            raise ConnectionError("gone")

    await bridge.connect("phone", BrokenWS())
    result = await bridge.broadcast_action("TRIGGER_SMS", {})
    assert result["recipients"] == 0 and result["status"] == "failed"


async def test_each_event_loop_gets_its_own_mongo_client(monkeypatch):
    created = []

    class FakeMotorClient:
        def __init__(self, *args, **kwargs):
            created.append(self)

        def __getitem__(self, name):
            return ("database", id(self))

        def close(self):
            pass

    monkeypatch.setattr(mongo, "AsyncIOMotorClient", FakeMotorClient)
    database = mongo.Database()
    monkeypatch.setattr(database, "_loop_databases", {})
    monkeypatch.setattr(database, "_loop_clients", [])
    database.is_connected = True
    database.db = "server-loop-db"

    async def read_db():
        return database.db

    assert database.db == "server-loop-db"
    worker_db = await AgentWorker().run(read_db)
    assert worker_db != "server-loop-db" and len(created) == 1
    assert database.db == "server-loop-db"  # server loop keeps its own client
