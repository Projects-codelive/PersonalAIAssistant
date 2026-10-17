import asyncio
import logging
import threading
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class AgentWorker:
    """
    Runs the agent pipeline on one dedicated thread with its own event loop.

    The agent drives the phone with synchronous ADB calls and `time.sleep` pauses, and some knowledge
    tools (Wikipedia, DuckDuckGo, `ollama.generate`) are synchronous too. On the server's event loop those
    would freeze every other request — status checks, the live screen view and phone WebSockets — for
    the whole length of a task. Here they only block this worker.

    A single worker also keeps agent tasks serialized: there is one phone to drive, and the agent's
    in-memory session state (pending confirmations, conversation memory) is not thread-safe.
    """

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def _ensure_started(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, name="agent-worker", daemon=True)
                thread.start()
                self._loop = loop
                logger.info("Started agent worker thread")
            return self._loop

    async def run(self, coro_fn: Callable[..., Awaitable[Any]], *args, **kwargs) -> Any:
        """Await `coro_fn(*args, **kwargs)` executed on the worker loop, without blocking the caller's loop"""
        loop = self._ensure_started()
        future = asyncio.run_coroutine_threadsafe(coro_fn(*args, **kwargs), loop)
        return await asyncio.wrap_future(future)


agent_worker = AgentWorker()
