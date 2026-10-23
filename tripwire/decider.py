"""A tiny synchronous client for a running `strands-decider serve` endpoint.

Adapted from strands-labs/strands-decider `examples/strands/_client.py` (Apache-2.0).
Stdlib only, so the patterns in this package can be dropped into any agent without
adding a dependency.

    strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Two ways to run the decider, same typed answers:
# - local:  `strands-decider serve ... --port 8099`, POST /v1/systemone, no key (the default);
# - hosted: the AWS User Group Madurai community API (https://www.awsugmdu.in), POST /v1/decide
#           with `Authorization: Bearer $DECIDER_API_KEY`. Setting the key switches to hosted.
API_KEY = os.environ.get("DECIDER_API_KEY") or os.environ.get("STRANDS_DECIDER_API_KEY") or ""
HOSTED_URL = "https://da25tnxy06gjk.cloudfront.net"
DEFAULT_URL = os.environ.get("STRANDS_DECIDER_URL") or (HOSTED_URL if API_KEY else "http://127.0.0.1:8099")


class DeciderUnavailable(RuntimeError):
    """Nothing is answering at the decider URL."""


@dataclass
class DeciderStats:
    calls: int = 0
    input_tokens: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def median_ms(self) -> float:
        xs = sorted(self.latencies_ms)
        return xs[len(xs) // 2] if xs else 0.0


class Decider:
    """One state, many typed questions, one round trip.

    The state is encoded once and shared by every question in the request, so ask
    everything you need about one state in a single `ask` call.
    """

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 120.0, api_key: str = API_KEY) -> None:
        self.url = url.rstrip("/")
        local = "127.0.0.1" in self.url or "localhost" in self.url
        self.api_key = "" if local else api_key
        self.hosted = bool(self.api_key)
        self.timeout = timeout
        self.stats = DeciderStats()
        self.last_latency_ms: float = 0.0
        self._lock = threading.Lock()  # the server is best driven one request at a time

    # ---- question builders -------------------------------------------------

    @staticmethod
    def noul(instructions: str, true: str | None = None, false: str | None = None) -> dict[str, Any]:
        """Yes/no question. The answer is P(true) in 0..1."""
        q: dict[str, Any] = {"type": "noul", "instructions": instructions}
        if true or false:
            q["criteria"] = {"true": true, "false": false}
        return q

    @staticmethod
    def choice(instructions: str, options: Mapping[str, str | None]) -> dict[str, Any]:
        """Pick one of N. `options` maps option name -> what it means."""
        return {"type": "choice", "instructions": instructions, "criteria": dict(options)}

    @staticmethod
    def score(instructions: str, levels: list[str]) -> dict[str, Any]:
        """Rate on an ordered rubric, lowest level first."""
        return {"type": "score", "instructions": instructions, "criteria": list(levels)}

    # ---- asking -----------------------------------------------------------

    def ask(self, state: Any, questions: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
        """Evaluate `state` against `questions`; answers come back under the same keys."""
        return self.ask_timed(state, questions)[0]

    def ask_timed(self, state: Any, questions: Mapping[str, dict[str, Any]]) -> tuple[dict[str, Any], float]:
        """Like `ask`, plus the server-reported latency in ms for this request."""
        body = json.dumps({"state": state, "questions": dict(questions)}).encode()
        path = "/v1/decide" if self.hosted else "/v1/systemone"
        req = urllib.request.Request(f"{self.url}{path}", data=body, headers=self._headers())
        t0 = time.perf_counter()
        try:
            with self._lock, urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            hint = {401: "check DECIDER_API_KEY", 402: "monthly quota used up", 429: "rate limited"}.get(exc.code, "")
            raise DeciderUnavailable(f"decider at {self.url} returned HTTP {exc.code} {hint}") from exc
        except urllib.error.URLError as exc:
            raise DeciderUnavailable(
                f"no strands-decider server at {self.url} ({exc.reason}). Start one with:\n"
                "  strands-decider serve StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 --port 8099"
            ) from exc
        ms = float(payload.get("latency_ms") or (time.perf_counter() - t0) * 1000)
        self.last_latency_ms = ms
        self.stats.calls += 1
        self.stats.input_tokens += payload.get("usage", {}).get("input_tokens", 0)
        self.stats.latencies_ms.append(ms)
        return payload["answers"], ms

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def health(self) -> dict[str, Any]:
        path = "/v1/models" if self.hosted else "/health"
        req = urllib.request.Request(f"{self.url}{path}", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise DeciderUnavailable(f"no strands-decider server at {self.url}") from exc
