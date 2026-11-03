import httpx

from app.config import Settings
from app.http import Sleeper, send
from app.sources.opensky import UpstreamError


async def fetch_report(
    http: httpx.AsyncClient,
    settings: Settings,
    sleeper: Sleeper,
    product: str,
    station: str,
) -> httpx.Response:
    try:
        return await send(
            http,
            "GET",
            f"{settings.aviationweather_api_url}/{product}",
            "aviationweather",
            sleeper,
            params={"ids": station, "format": "json"},
        )
    except httpx.TimeoutException as exc:
        raise UpstreamError("timeout") from exc
