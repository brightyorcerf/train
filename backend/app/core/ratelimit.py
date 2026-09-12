"""Global per-provider rate limiting in Redis (§8): a token bucket shared by every worker.

Pacing inside one process is not enough once a Celery chord fans a frontier out across workers —
that is exactly how a free tier turns into a ban. The bucket is refilled by elapsed time and
atomically drained by a Lua script, so N workers hitting one provider still obey one rate.

Measured ceilings this limiter is configured from:
  etherscan      3 req/s server-enforced; we pace 2.5/s (jitter still trips ~15%, day 1)
  mempool        no published limit; throttles bursts -> 2 req/s
  blockstream    700 req/hour/IP unauthenticated since 2025-07-15 (its own 429 body) -> 0.19 req/s
Daily quota (Etherscan 100k/day) is a separate counter: it is the ceiling that actually bites.
"""
import time

import redis

from app.core.config import settings

# name -> (tokens per second, bucket capacity)
LIMITS = {"etherscan": (2.5, 1), "mempool": (2.0, 2), "blockstream": (700 / 3600, 5)}
DAILY = {"etherscan": 100_000}

_TAKE = """
local key, rate, cap, cost, now = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
local b = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(b[1]) or cap
local ts = tonumber(b[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)
if tokens >= cost then
  redis.call('HMSET', key, 'tokens', tokens - cost, 'ts', now)
  redis.call('EXPIRE', key, 3600)
  return 0
end
redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, 3600)
return math.ceil((cost - tokens) / rate * 1000)   -- ms to wait
"""


class RateLimiter:
    """acquire(name) blocks until this process may make one call to that provider."""

    def __init__(self, url: str | None = None, limits: dict | None = None, prefix="rl"):
        self.r = redis.Redis.from_url(url or settings.redis_url)
        self.limits = limits or LIMITS
        self.prefix = prefix
        self._take = self.r.register_script(_TAKE)
        self.waited = 0.0
        self.waits = 0

    def acquire(self, name: str, cost: int = 1, max_wait: float = 60.0) -> float:
        rate, cap = self.limits.get(name, (2.0, 1))
        t0 = time.time()
        while True:
            ms = int(self._take(keys=[f"{self.prefix}:{name}"], args=[rate, cap, cost, time.time()]))
            if ms == 0:
                w = time.time() - t0
                self.waited += w
                self.waits += w > 0.001
                return w
            if time.time() - t0 + ms / 1000 > max_wait:
                raise TimeoutError(f"rate limiter: {name} still saturated after {max_wait}s")
            time.sleep(min(ms / 1000, 1.0))

    def spend_quota(self, name: str, n: int = 1) -> int:
        """Daily call counter (UTC day). Raises once the free quota is gone — better than a silent ban."""
        limit = DAILY.get(name)
        if not limit:
            return 0
        k = f"{self.prefix}:quota:{name}:{time.strftime('%Y-%m-%d', time.gmtime())}"
        used = self.r.incrby(k, n)
        self.r.expire(k, 172800)
        if used > limit:
            raise RuntimeError(f"{name}: daily free quota {limit} exhausted ({used} used)")
        return used

    def used_today(self, name: str) -> int:
        k = f"{self.prefix}:quota:{name}:{time.strftime('%Y-%m-%d', time.gmtime())}"
        return int(self.r.get(k) or 0)


def open_limiter(url: str | None = None):
    try:
        rl = RateLimiter(url)
        rl.r.ping()
        return rl
    except Exception as e:  # noqa: BLE001
        print(f"   ! rate limiter unavailable ({type(e).__name__}); per-process pacing only")
        return None
