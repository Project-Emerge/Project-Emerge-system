"""Non-blocking queue draining, shared by the process pump and the MQTT listener."""

from __future__ import annotations

import queue


def drain[T](source: queue.SimpleQueue[T], limit: int) -> list[T]:
    """Take up to ``limit`` items already queued, without ever blocking."""
    items: list[T] = []
    while len(items) < limit:
        try:
            items.append(source.get_nowait())
        except queue.Empty:
            break
    return items
