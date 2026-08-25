"""In-process metrics for /api/metrics.

Deliberately small: counters plus bounded latency samples. No Prometheus client
dependency, because a single-process app does not need one and an endpoint that
returns JSON is easier to read in a demo.

The split that matters here is tool_ms vs model time per turn — tool_ms is the
time inside tool HANDLERS (database, Gmail, embeddings), which on this workload
is ~2.5% of turn latency against the model's ~97.5% — measured, not assumed.
Exposing it keeps that honest, and stops anyone (including me) from "optimising"
the query layer to save 3ms of a 10-second turn. (These keys used to say db_ms,
which claimed more precision than the measurement has: a slow Gmail day would
have read as a slow database.)
"""

from __future__ import annotations

import threading
from collections import Counter, deque
from statistics import median

MAX_SAMPLES = 500


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: Counter[str] = Counter()
        self.turn_ms: deque[float] = deque(maxlen=MAX_SAMPLES)
        self.tool_ms: deque[float] = deque(maxlen=MAX_SAMPLES)
        self.tool_calls: Counter[str] = Counter()
        self.tool_errors: Counter[str] = Counter()
        self.tokens: Counter[str] = Counter()

    def incr(self, name: str, by: int = 1) -> None:
        with self._lock:
            self.counters[name] += by

    def record_turn(
        self,
        *,
        total_ms: float,
        tool_total_ms: float,
        tools: list[tuple[str, bool]],
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        with self._lock:
            self.counters["turns"] += 1
            self.turn_ms.append(total_ms)
            self.tool_ms.append(tool_total_ms)
            for name, is_error in tools:
                self.tool_calls[name] += 1
                if is_error:
                    self.tool_errors[name] += 1
            self.tokens["input"] += input_tokens
            self.tokens["output"] += output_tokens
            self.tokens["cached"] += cached_tokens

    @staticmethod
    def _pct(samples: list[float], fraction: float) -> float | None:
        if not samples:
            return None
        ordered = sorted(samples)
        index = min(len(ordered) - 1, int(len(ordered) * fraction))
        return round(ordered[index], 1)

    def snapshot(self) -> dict:
        with self._lock:
            turns = list(self.turn_ms)
            tools_ms = list(self.tool_ms)
            counters = dict(self.counters)
            tool_calls = dict(self.tool_calls)
            tool_errors = dict(self.tool_errors)
            tokens = dict(self.tokens)

        tool_share = None
        if turns and tools_ms and sum(turns) > 0:
            tool_share = round(100.0 * sum(tools_ms) / sum(turns), 2)

        return {
            "counters": counters,
            "latency_ms": {
                "turn_p50": round(median(turns), 1) if turns else None,
                "turn_p95": self._pct(turns, 0.95),
                "turn_max": round(max(turns), 1) if turns else None,
                "tool_p50": round(median(tools_ms), 1) if tools_ms else None,
                "tool_p95": self._pct(tools_ms, 0.95),
                "samples": len(turns),
            },
            # The headline number: how much of a turn is actually the tools.
            "tool_share_of_turn_pct": tool_share,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "tokens": tokens,
        }


metrics = Metrics()
