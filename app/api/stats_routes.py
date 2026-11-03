from fastapi import APIRouter, HTTPException, Query, Request

from app.services.stats import sync_faa_enplanements, sync_ontime, sync_t100
from app.sources.opensky import UpstreamError

router = APIRouter()


def _session(request: Request):
    return request.app.state.session_factory()


@router.post("/sources/faa/enplanements")
async def faa_enplanements(request: Request):
    session = _session(request)
    try:
        return await sync_faa_enplanements(
            session,
            request.app.state.http,
            request.app.state.settings,
            request.app.state.sleeper,
        )
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    finally:
        session.close()


@router.post("/sources/bts/t100")
async def bts_t100(request: Request, year: int = Query(ge=1990, le=2100), month: int = Query(ge=1, le=12)):
    session = _session(request)
    try:
        return await sync_t100(
            session,
            request.app.state.http,
            request.app.state.settings,
            request.app.state.sleeper,
            year,
            month,
        )
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    finally:
        session.close()


@router.post("/sources/bts/ontime")
async def bts_ontime(request: Request, year: int = Query(ge=1987, le=2100), month: int = Query(ge=1, le=12)):
    session = _session(request)
    try:
        return await sync_ontime(
            session,
            request.app.state.http,
            request.app.state.settings,
            request.app.state.sleeper,
            year,
            month,
        )
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    finally:
        session.close()
