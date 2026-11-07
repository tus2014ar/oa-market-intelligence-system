"""A small bounded cache for the API's answers (DL-127).

Everything the API serves is a pure function of the loaded data (the snapshot and the saved models)
and the request, and the data change only when the service switches to a new release. So an answer
can be kept and reused until then. The load test (docs/loadtest.md) showed the API was spending its
time recomputing the same answers.

Rules that keep it safe:
- **Bounded.** At most `maxsize` entries, the oldest dropped first, so a client that sends many
  different requests (for example every search word) cannot make it grow.
- **Private copies.** What is stored and what is returned are copies, so nothing a caller does to
  an answer can change the next answer.
- **Never an error.** Only successful answers are stored; the caller stores nothing when the
  computation raises.
- **Thread-safe.** The API's endpoints run in worker threads.
- **Per release.** The key includes a generation number that the app raises whenever it switches
  its data, so an answer from the old data can never be returned for the new data.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable

DEFAULT_MAXSIZE = 512


class BoundedCache:
    def __init__(self, maxsize: int = DEFAULT_MAXSIZE):
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self.maxsize = maxsize
        self._items: OrderedDict[Hashable, object] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get_or_compute(self, key: Hashable, compute: Callable[[], dict]):
        """The answer for `key`: a copy of the stored one, or `compute()`'s (stored as a copy).
        Whatever `compute` raises is passed on and nothing is stored."""
        with self._lock:
            if key in self._items:
                self.hits += 1
                return copy.deepcopy(self._items[key])
            self.misses += 1
        value = compute()  # outside the lock: a slow answer does not block the others
        with self._lock:
            self._items[key] = copy.deepcopy(value)
            self._items.move_to_end(key)
            while len(self._items) > self.maxsize:
                self._items.popitem(last=False)  # the oldest first
        return value
