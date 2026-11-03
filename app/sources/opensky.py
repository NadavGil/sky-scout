import json
from datetime import timedelta

import httpx

from app import clock
from app.config import Settings
from app.http import Sleeper, send


class UpstreamError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class OpenSkyClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings, sleeper: Sleeper):
        self.http = http
        self.settings = settings
        self.sleeper = sleeper
        self._token: str | None = None
        self._token_until = None

    async def states(self, lamin: float, lomin: float, lamax: float, lomax: float) -> httpx.Response:
        params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}
        return await self._authed_get("/states/all", params, auth=self.settings.opensky_flights_configured)

    async def flights(self, kind: str, airport: str, begin: int, end: int) -> httpx.Response:
        path = "/flights/arrival" if kind == "arrival" else "/flights/departure"
        return await self._authed_get(path, {"airport": airport, "begin": begin, "end": end}, auth=True)

    async def _authed_get(self, path: str, params: dict, auth: bool) -> httpx.Response:
        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {await self._token_value()}"
        response = await self._get(path, params, headers)
        if response.status_code == 401 and auth:
            headers["Authorization"] = f"Bearer {await self._token_value(force=True)}"
            response = await self._get(path, params, headers)
        return response

    async def _get(self, path: str, params: dict, headers: dict) -> httpx.Response:
        try:
            return await send(
                self.http,
                "GET",
                f"{self.settings.opensky_api_url}{path}",
                "opensky",
                self.sleeper,
                params=params,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise UpstreamError("timeout") from exc

    async def _token_value(self, force: bool = False) -> str:
        if (
            not force
            and self._token
            and self._token_until is not None
            and self._token_until > clock.utcnow() + timedelta(seconds=60)
        ):
            return self._token
        try:
            response = await send(
                self.http,
                "POST",
                self.settings.opensky_token_url,
                "opensky",
                self.sleeper,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.opensky_client_id,
                    "client_secret": self.settings.opensky_client_secret,
                },
            )
        except httpx.TimeoutException as exc:
            raise UpstreamError("timeout") from exc
        if response.status_code != 200:
            raise UpstreamError(f"HTTP {response.status_code}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise UpstreamError("OpenSky token response had no access_token")
        expires_in = int(payload.get("expires_in") or 1800)
        self._token = token
        self._token_until = clock.utcnow() + timedelta(seconds=expires_in)
        return token


def interpret_states(payload: object) -> tuple[float | None, str]:
    if not isinstance(payload, dict) or "states" not in payload or payload["states"] is None:
        return None, "missing_states"
    states = payload["states"]
    if not isinstance(states, list):
        return None, "missing_states"
    return float(len(states)), "ok"


def interpret_flights(response: httpx.Response) -> tuple[float | None, str, bool]:
    if response.status_code == 404 or not response.content:
        return None, "no_rows", True
    if response.status_code != 200:
        raise UpstreamError(f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise UpstreamError("OpenSky response was not JSON") from exc
    if payload is None:
        return None, "no_rows", True
    if isinstance(payload, list):
        return float(len(payload)), "ok", True
    raise UpstreamError("OpenSky flight response was not a list")
