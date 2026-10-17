"""Smooth degradation: MongoDB outages don't slow every request; the LLM is configured for fast replies."""
import time

import app.db.mongo as mongo
from app.agent.gemma_agent import create_local_gemma, OLLAMA_KEEP_ALIVE


class DeadCollection:
    """A MongoDB collection whose every call fails like a refused connection, counting attempts"""

    def __init__(self):
        self.attempts = 0

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            self.attempts += 1
            raise ConnectionRefusedError("localhost:27017: [Errno 61] Connection refused")
        return fail


class DeadDatabase:
    def __init__(self):
        self.contacts = DeadCollection()
        self.conversations = DeadCollection()


def break_mongo(monkeypatch):
    dead = DeadDatabase()
    monkeypatch.setattr(mongo.db, "_loop_databases", {})
    monkeypatch.setattr(type(mongo.db), "db", property(lambda self: dead))
    monkeypatch.setattr(mongo.db, "_connected", True)
    monkeypatch.setattr(mongo.db, "_unavailable_until", 0.0)
    return dead


async def test_mongo_failure_trips_breaker_and_falls_back_to_memory(monkeypatch):
    dead = break_mongo(monkeypatch)
    first = await mongo.get_contacts()
    assert first == mongo.db._memory_contacts            # fallback data, not an error
    assert dead.contacts.attempts == 1
    assert mongo.db.is_connected is False                # breaker open

    for _ in range(5):                                   # later calls skip MongoDB entirely
        await mongo.get_contacts()
        await mongo.save_conversation_turn("s", "user", "hi")
    assert dead.contacts.attempts == 1 and dead.conversations.attempts == 0


async def test_mongo_is_retried_after_cool_down(monkeypatch):
    dead = break_mongo(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(mongo.time, "monotonic", lambda: clock[0])
    await mongo.get_contacts()
    assert dead.contacts.attempts == 1
    clock[0] += mongo.Database.RETRY_AFTER_SECONDS + 1
    assert mongo.db.is_connected is True                 # half-open: try MongoDB again
    await mongo.get_contacts()
    assert dead.contacts.attempts == 2


def test_successful_reconnect_closes_breaker(monkeypatch):
    monkeypatch.setattr(mongo.db, "_connected", False)  # restored after the test (suite stays offline)
    monkeypatch.setattr(mongo.db, "_unavailable_until", time.monotonic() + 999)
    mongo.db.is_connected = True
    assert mongo.db.is_connected is True


def test_llm_configured_for_fast_replies():
    llm = create_local_gemma()
    assert llm.reasoning is False          # no chain-of-thought: ~0.4s instead of ~11s per call
    assert llm.keep_alive == OLLAMA_KEEP_ALIVE
