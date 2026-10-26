"""Live model health in Redis: latency, error rates and a circuit breaker per model.

Every upstream outcome is recorded with one atomic script that updates a per-minute counter
bucket, a ring of recent latencies and the breaker. Shared state means every gateway replica
sees the same health and the same open circuits.

Breaker:
  closed     normal; opens after `failure_threshold` consecutive failures, or when the error
             rate over the window exceeds `error_rate_threshold` with enough samples
  open       skipped by the router; after `cooldown_s` the next read reports half_open
  half_open  exactly one probe request is allowed through (a short lock); success closes
             the circuit, failure re-opens it for another cooldown
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from smartrouter.routing.engine import HealthView

WINDOW_MIN = 5
LATENCY_SAMPLES = 200
OUTCOMES = ("ok", "error", "timeout", "throttled")

# KEYS: bucket, latencies, breaker. ARGV: outcome, latency_ms, now, threshold,
# error_rate_threshold, min_samples, bucket_ttl, prev buckets... (for the error rate)
_RECORD = """
local outcome, lat, now = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
local threshold, rate_threshold, min_samples = tonumber(ARGV[4]), tonumber(ARGV[5]), tonumber(ARGV[6])
redis.call('HINCRBY', KEYS[1], outcome, 1)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[7]))
if outcome == 'ok' and lat >= 0 then
  redis.call('LPUSH', KEYS[2], lat)
  redis.call('LTRIM', KEYS[2], 0, 199)
end
local state = redis.call('HGET', KEYS[3], 'state') or 'closed'
if outcome == 'ok' then
  redis.call('HSET', KEYS[3], 'consecutive', 0)
  if state ~= 'closed' then
    redis.call('HSET', KEYS[3], 'state', 'closed', 'changed_at', now)
    return 'closed'
  end
  return state
end
local consecutive = redis.call('HINCRBY', KEYS[3], 'consecutive', 1)
if state == 'half_open' or state == 'open' then
  redis.call('HSET', KEYS[3], 'state', 'open', 'opened_at', now, 'changed_at', now)
  return 'open'
end
local total, failed = 0, 0
for i = 8, #ARGV do
  local b = redis.call('HGETALL', ARGV[i])
  for j = 1, #b, 2 do
    local n = tonumber(b[j + 1])
    total = total + n
    if b[j] ~= 'ok' then failed = failed + n end
  end
end
if consecutive >= threshold or (total >= min_samples and failed / total > rate_threshold) then
  redis.call('HSET', KEYS[3], 'state', 'open', 'opened_at', now, 'changed_at', now)
  return 'open'
end
return 'closed'
"""


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    failure_threshold: int = 5
    error_rate_threshold: float = 0.5
    min_samples: int = 10
    cooldown_s: int = 30


def _minute(now: float) -> int:
    return int(now // 60)


class HealthTracker:
    def __init__(self, redis: Redis, config: BreakerConfig | None = None) -> None:
        self._redis = redis
        self.config = config or BreakerConfig()
        self._record = redis.register_script(_RECORD)

    @staticmethod
    def _bucket(alias: str, minute: int) -> str:
        return f"sr:h:{alias}:{minute}"

    async def record(self, alias: str, outcome: str, latency_ms: float) -> str:
        """Record one upstream outcome; returns the breaker state afterwards. A negative
        latency records the outcome without a latency sample (health probes)."""
        now = time.time()
        minute = _minute(now)
        recent = [self._bucket(alias, minute - i) for i in range(WINDOW_MIN)]
        try:
            state = await self._record(
                keys=[recent[0], f"sr:hl:{alias}", f"sr:cb:{alias}"],
                args=[
                    outcome,
                    int(latency_ms),
                    int(now),
                    self.config.failure_threshold,
                    self.config.error_rate_threshold,
                    self.config.min_samples,
                    (WINDOW_MIN + 1) * 60,
                    *recent,
                ],
            )
        except RedisError:
            return "unknown"
        return state.decode() if isinstance(state, bytes) else str(state)

    async def snapshot(self, aliases: Sequence[str]) -> dict[str, HealthView]:
        """Health for every model in one pipeline round trip."""
        if not aliases:
            return {}
        now = time.time()
        minute = _minute(now)
        pipe = self._redis.pipeline(transaction=False)
        for alias in aliases:
            pipe.hgetall(f"sr:cb:{alias}")
            pipe.lrange(f"sr:hl:{alias}", 0, LATENCY_SAMPLES - 1)
            for i in range(WINDOW_MIN):
                pipe.hgetall(self._bucket(alias, minute - i))
        try:
            raw = await pipe.execute()
        except RedisError:
            return {}
        out: dict[str, HealthView] = {}
        step = 2 + WINDOW_MIN
        for idx, alias in enumerate(aliases):
            breaker, lats, *buckets = raw[idx * step : (idx + 1) * step]
            state = _decode(breaker.get(b"state")) or "closed"
            if state == "open":
                opened = float(_decode(breaker.get(b"opened_at")) or 0)
                if now - opened >= self.config.cooldown_s:
                    state = "half_open"
            samples = sorted(float(x) for x in lats)
            total = failed = 0
            for b in buckets:
                for k, v in b.items():
                    total += int(v)
                    if k != b"ok":
                        failed += int(v)
            out[alias] = HealthView(
                state=state,
                p50_ms=_pct(samples, 0.5),
                p95_ms=_pct(samples, 0.95),
                error_rate=(failed / total) if total else 0.0,
                samples=len(samples),
            )
        return out

    async def try_probe(self, alias: str) -> bool:
        """In half_open, only one request at a time may test the model."""
        try:
            return bool(
                await self._redis.set(f"sr:cbp:{alias}", b"1", nx=True, ex=self.config.cooldown_s)
            )
        except RedisError:
            return False

    async def window_counts(self, alias: str) -> dict[str, int]:
        minute = _minute(time.time())
        pipe = self._redis.pipeline(transaction=False)
        for i in range(WINDOW_MIN):
            pipe.hgetall(self._bucket(alias, minute - i))
        counts = dict.fromkeys(OUTCOMES, 0)
        for b in await pipe.execute():
            for k, v in b.items():
                counts[_decode(k) or "ok"] = counts.get(_decode(k) or "ok", 0) + int(v)
        return counts

    async def reset(self, alias: str) -> None:
        await self._redis.delete(f"sr:cb:{alias}", f"sr:cbp:{alias}")


def _decode(v: bytes | str | None) -> str | None:
    if v is None:
        return None
    return v.decode() if isinstance(v, bytes) else v


def _pct(samples: list[float], q: float) -> float | None:
    if not samples:
        return None
    idx = min(len(samples) - 1, round(q * (len(samples) - 1)))
    return samples[idx]
