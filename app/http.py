import logging
import time
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger("sky_scout")

Sleeper = Callable[[float], Awaitable[None]]


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "1")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0


async def send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    source: str,
    sleeper: Sleeper,
    **kwargs,
) -> httpx.Response:
    response = await _once(client, method, url, source, **kwargs)
    if response.status_code != 429:
        return response
    await sleeper(_retry_after(response))
    return await _once(client, method, url, source, **kwargs)


async def _once(client: httpx.AsyncClient, method: str, url: str, source: str, **kwargs) -> httpx.Response:
    started = time.perf_counter()
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        logger.info("source=%s status=timeout latency_ms=%s", source, elapsed)
        raise
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    logger.info("source=%s status=%s latency_ms=%s", source, response.status_code, elapsed)
    return response
