"""The read-only API (DL-116): FastAPI over the published warehouse.

Every endpoint is a thin, typed wrapper over the lookups the AI Question Box uses
(`serving.qa_tools`), which are built from the same functions as the website's pages, so the API,
the site and the box cannot disagree. It reads aggregates only, opens the database read-only and
fits nothing; the monthly models stay batch jobs and the API serves their stored output.

Access: if `API_KEYS` (comma-separated) is set, every /v1 endpoint needs an `X-API-Key` header with
one of them; if it is not set the API is open, which `/health` says. Each client (a key, or an
address when open) may make `API_RATE_PER_MINUTE` requests a minute (default 60). `/health` needs
no key. Logs are one JSON line per request and never contain a key.

    uvicorn oa_market_intelligence.api.main:create_app --factory --port 8000
    oa-api                                    (the same, with PORT and HOST from the environment)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from oa_market_intelligence.api import schemas
from oa_market_intelligence.api.cache import BoundedCache
from oa_market_intelligence.api.remote_data import RemoteData, Source
from oa_market_intelligence.serving import data, guard, qa_tools
from oa_market_intelligence.serving.predictor import Predictor

VERSION = "1.0.0"
DEFAULT_RATE_PER_MINUTE = 60
log = logging.getLogger("oa_api")

DESCRIPTION = """
Zilretta's share of visits against generic steroid injections and NSAID/OTC products, from
aggregated IQVIA NMTA visit counts, with the stored forecasts and how they scored in testing.
It serves the same figures as the website's pages.

**Read these before quoting a number.**
- These are visits in IQVIA's panel, not company sales, and nothing here says *why* a share moves.
- The newest month is Jul 2025: the forecast months have already passed (see `/v1/status`).
- The national forecast is "same as last month": no model beat that simple rule.
- Segment forecasts run high in testing; the decline alert is a 1-month supporting flag only.
- Only aggregates are served. No patient-level or provider-level rows exist in this API.
- `/v1/predict/*` runs the saved models for a segment when you ask. The result must equal the
  monthly job's stored forecast, and each response says whether it does. It is not a what-if tool.
"""

TAGS = [
    {"name": "service", "description": "Health of the service."},
    {"name": "data", "description": "The market series, segments and headline numbers."},
    {"name": "forecasts", "description": "The stored forecasts and the decline alert."},
    {"name": "models", "description": "How the models scored, and what to use each for."},
    {
        "name": "predictions",
        "description": "Live predictions: the saved models are run when you ask (see /v1/models).",
    },
    {"name": "findings", "description": "The numbered findings with evidence and impact."},
]
ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": schemas.Problem, "description": "A missing or wrong X-API-Key."},
    422: {"model": schemas.Problem, "description": "A value the data cannot answer."},
    429: {"model": schemas.Problem, "description": "Too many requests from this client."},
}


def _client(request: Request, key: str | None) -> str:
    """Who a request counts against: a short hash of the key, or the caller's address."""
    if key:
        return "key:" + hashlib.sha256(key.encode()).hexdigest()[:12]
    return "ip:" + (request.client.host if request.client else "unknown")


def guarded(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Check the API key (when keys are configured) and the client's rate."""
    state = request.app.state
    if state.keys and not (
        x_api_key and any(hmac.compare_digest(x_api_key, key) for key in state.keys)
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid X-API-Key header is required.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not state.limiter.allow(_client(request, x_api_key)):
        raise HTTPException(
            status_code=429, detail="Too many requests; slow down.", headers={"Retry-After": "60"}
        )


def cached(request: Request, key: tuple, compute):
    """An answer kept until the data change (DL-127): the cache is bounded, hands out copies, never
    stores an error, and its key includes the generation, which rises whenever the data switch."""
    state = request.app.state
    return state.cache.get_or_compute((state.generation, *key), compute)


def lookup(request: Request, tool: str, arguments: dict | None = None) -> dict:
    """Run one of the box's read-only lookups; what the data cannot answer becomes a clean error."""
    args = arguments or {}

    def compute() -> dict:
        out = qa_tools.run_tool(tool, args, snapshot=request.app.state.snapshot)
        if "error" in out:
            code = 404 if out["error"].startswith("No months") else 422
            raise HTTPException(status_code=code, detail=out["error"])
        return out

    return cached(request, ("tool", tool, json.dumps(args, sort_keys=True, default=str)), compute)


def _log_to_stdout() -> None:
    """Send the access log to standard output as plain JSON lines (once, however many apps)."""
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)


SEGMENT_NOTE = (
    "Segment forecasts ran high in testing (see known_bias_pct): read this as optimistic, and as a "
    "lead to investigate, not proof. At 6 months the model is 'same as last month'. Small "
    "segments are noisy."
)
ALERT_NOTE = (
    "A 1-month supporting flag only. About half of the flags were real declines in testing, and "
    "much of that is a segment returning to its usual level: use it to decide where to look."
)


def _stored(request: Request, specialty: str, age_band: str, gender: str, horizon: int):
    """The batch job's row for the same segment and horizon, or None."""
    pred = request.app.state.snapshot.pred
    rows = pred[(pred["level"] == "segment") & (pred["horizon"] == horizon)]
    mask = (
        (rows["specialty_name"].str.upper() == specialty.upper())
        & (rows["age_band"].str.upper() == age_band.upper())
        & (rows["gender"].str.upper() == gender.upper())
    )
    return rows[mask].iloc[0] if mask.any() else None


def _data_version(snapshot) -> str:
    """The data version the stored forecasts carry ('unknown' if none, 'mixed' if they differ)."""
    versions = snapshot.pred["data_version"].dropna().astype(str).unique()
    return str(versions[0]) if len(versions) == 1 else ("mixed" if len(versions) else "unknown")


def _image_source() -> Source:
    """The copy of the warehouse in the image (its version is read from the data when needed)."""
    return Source(data.DB_PATH, "", "image", "")


def _load(source: Source, live_predictions: bool):
    """The snapshot (and the saved models, if wanted) from a warehouse file."""
    engine = data.open_engine(source.path)
    try:
        snapshot = qa_tools.load_snapshot(engine, run=source.run)
        predictor = None
        if live_predictions:
            artifacts, inputs = data.saved_models(engine)
            if not artifacts.empty and not inputs.empty:
                predictor = Predictor(artifacts, inputs)
    finally:
        engine.dispose()
    return snapshot, predictor


def _refresh(app: FastAPI, remote: RemoteData, live_predictions: bool) -> None:
    """Switch to a newer verified release, if there is one. Anything that goes wrong leaves the
    data being served as it is."""
    newer = remote.poll(app.state.source)
    if newer is None:
        return
    try:
        snapshot, predictor = _load(newer, live_predictions)
    except Exception:  # noqa: BLE001 - a file the code cannot use must not replace a working one
        log.warning("a new release was found but could not be loaded: keeping the current data")
        return
    app.state.snapshot, app.state.predictor, app.state.source = snapshot, predictor, newer
    app.state.generation += 1
    log.info("now serving data version %s from %s", newer.data_version, newer.origin)


def predictor_of(request: Request) -> Predictor:
    """The live predictor, or a clean 503 when this warehouse has no saved models."""
    predictor = request.app.state.predictor
    if predictor is None:
        raise HTTPException(
            status_code=503, detail="Live predictions are not available: no saved models."
        )
    return predictor


def _not_found(error: LookupError) -> HTTPException:
    message = str(error)
    code = 404 if message.startswith("No segment") else 422
    return HTTPException(status_code=code, detail=message)


def _keys_from_environment() -> list[str]:
    return [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]


def create_app(
    snapshot: qa_tools.Snapshot | None = None,
    api_keys: list[str] | None = None,
    rate_per_minute: int | None = None,
    predictor: Predictor | None = None,
    live_predictions: bool = True,
    remote: RemoteData | None = None,
) -> FastAPI:
    """The app. Tests pass a snapshot, keys and a predictor; the service loads the published
    warehouse. `live_predictions=False` serves no saved models: /v1/predict says 503. With
    `OA_DATA_BUCKET` set (or a `remote` passed) it serves the current release in the data bucket
    and follows new ones, falling back to the copy in the image (DL-124)."""
    remote = remote if remote is not None else RemoteData.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if getattr(app.state, "snapshot", None) is None:
            source = remote.initial() if remote else _image_source()
            try:
                snapshot, loaded = _load(source, live_predictions)
            except Exception:  # noqa: BLE001 - a release the code cannot use: the image's copy
                if remote is None:
                    raise
                log.warning("the release could not be loaded: using the image's copy")
                source = remote.baked()
                snapshot, loaded = _load(source, live_predictions)
            app.state.snapshot, app.state.predictor, app.state.source = snapshot, loaded, source
        elif live_predictions and app.state.predictor is None:
            engine = data.open_engine()
            try:
                artifacts, inputs = data.saved_models(engine)
                if not artifacts.empty and not inputs.empty:
                    app.state.predictor = Predictor(artifacts, inputs)
            finally:
                engine.dispose()
        yield

    app = FastAPI(
        title="OA and RA Market Intelligence API",
        version=VERSION,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    _log_to_stdout()
    app.state.snapshot = snapshot
    app.state.predictor = predictor if live_predictions else None
    app.state.source = _image_source()
    app.state.cache = BoundedCache()
    app.state.generation = 0  # raised when the data switch, so cached answers are never reused
    app.state.keys = list(api_keys) if api_keys is not None else _keys_from_environment()
    per_minute = rate_per_minute or int(
        os.environ.get("API_RATE_PER_MINUTE", DEFAULT_RATE_PER_MINUTE)
    )
    app.state.limiter = guard.RateLimiter(max_per_window=per_minute, window_seconds=60)

    origins = [o.strip() for o in os.environ.get("API_CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"])

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        if remote is not None and remote.due():
            _refresh(request.app, remote, live_predictions)
        started = time.perf_counter()
        response = await call_next(request)
        log.info(
            json.dumps(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round(1000 * (time.perf_counter() - started), 1),
                }
            )
        )
        return response

    protected = [Depends(guarded)]

    @app.get("/health", response_model=schemas.Health, tags=["service"])
    def health(request: Request) -> dict:
        """Whether the service is up, how current its data are, and whether it needs a key."""

        def compute() -> dict:
            state = request.app.state
            status = qa_tools.run_tool("get_data_status", {}, snapshot=state.snapshot)
            return {
                "status": "ok",
                "version": VERSION,
                "data_through": status["latest_month"],
                "months_of_data": status["months_of_data"],
                "data_version": _data_version(state.snapshot),
                "data_source": state.source.origin,
                "auth": "api-key" if state.keys else "open",
            }

        return cached(request, ("health",), compute)

    @app.get(
        "/v1/status",
        response_model=schemas.Status,
        tags=["data"],
        dependencies=protected,
        responses=ERRORS,
    )
    def status(request: Request) -> dict:
        """The headline numbers, how fresh the data are, and the limits of the analysis."""
        return lookup(request, "get_data_status")

    @app.get(
        "/v1/share/monthly",
        response_model=schemas.ShareMonthly,
        tags=["data"],
        dependencies=protected,
        responses=ERRORS,
    )
    def share_monthly(
        request: Request,
        start_month_id: Annotated[
            int | None, Query(ge=201908, le=209912, description="First month, YYYYMM.")
        ] = None,
        end_month_id: Annotated[
            int | None, Query(ge=201908, le=209912, description="Last month, YYYYMM.")
        ] = None,
    ) -> dict:
        """Zilretta's share of visits by month, optionally limited to a range of months."""
        return lookup(
            request,
            "get_market_trend",
            {"start_month_id": start_month_id, "end_month_id": end_month_id},
        )

    @app.get(
        "/v1/share/yearly",
        response_model=schemas.ShareYearly,
        tags=["data"],
        dependencies=protected,
        responses=ERRORS,
    )
    def share_yearly(request: Request) -> dict:
        """Zilretta's share of visits by calendar year; the first and last years are part years."""
        return lookup(request, "get_market_trend", {"by_year": True})

    @app.get(
        "/v1/segments",
        response_model=schemas.Segments,
        tags=["data"],
        dependencies=protected,
        responses=ERRORS,
    )
    def segments(
        request: Request,
        by: Annotated[Literal["specialty", "age_band", "gender"], Query()] = "specialty",
        top: Annotated[int, Query(ge=1, le=qa_tools.MAX_TOP)] = 10,
    ) -> dict:
        """Share by specialty, age band or gender over all months (groups of 1,000+ visits)."""
        return lookup(request, "get_segments", {"by": by, "top": top})

    @app.get(
        "/v1/forecasts/national",
        response_model=schemas.NationalForecast,
        tags=["forecasts"],
        dependencies=protected,
        responses=ERRORS,
    )
    def forecasts_national(request: Request) -> dict:
        """The national share 1, 3 and 6 months ahead with its 90% range."""
        return lookup(request, "get_forecasts", {"view": "national"})

    @app.get(
        "/v1/forecasts/specialty",
        response_model=schemas.SpecialtyForecast,
        tags=["forecasts"],
        dependencies=protected,
        responses=ERRORS,
    )
    def forecasts_specialty(
        request: Request, top: Annotated[int, Query(ge=1, le=qa_tools.MAX_TOP)] = 10
    ) -> dict:
        """The segment forecasts rolled up to specialties, with the bias recorded in testing."""
        return lookup(request, "get_forecasts", {"view": "specialty", "top": top})

    @app.get(
        "/v1/forecasts/alerts",
        response_model=schemas.Alerts,
        tags=["forecasts"],
        dependencies=protected,
        responses=ERRORS,
    )
    def forecasts_alerts(
        request: Request, top: Annotated[int, Query(ge=1, le=qa_tools.MAX_TOP)] = 10
    ) -> dict:
        """The 1-month decline-alert list: a supporting flag, never a reason to act alone."""
        return lookup(request, "get_forecasts", {"view": "alerts", "top": top})

    @app.get(
        "/v1/model-results",
        response_model=schemas.ModelResults,
        tags=["models"],
        dependencies=protected,
        responses=ERRORS,
    )
    def model_results(request: Request) -> dict:
        """How each forecast scored against a simple rule, and what to use or not use it for."""
        return lookup(request, "get_model_results")

    @app.get(
        "/v1/findings",
        response_model=schemas.Findings,
        tags=["findings"],
        dependencies=protected,
        responses=ERRORS,
    )
    def findings(
        request: Request,
        keyword: Annotated[
            str | None,
            Query(max_length=60, description="A word in the finding, evidence or impact."),
        ] = None,
    ) -> dict:
        """The numbered findings with their evidence and business impact."""
        return lookup(request, "get_findings", {"keyword": keyword} if keyword else {})

    @app.get(
        "/v1/models",
        response_model=schemas.Models,
        tags=["predictions"],
        dependencies=protected,
        responses=ERRORS,
    )
    def models(request: Request) -> dict:
        """The saved models: what each is, what it was fitted on and a fingerprint of it."""
        predictor = predictor_of(request)

        def compute() -> dict:
            listed = predictor.models()
            return {
                "count": len(listed),
                "models": listed,
                "note": "The numbers behind each logistic model are in the warehouse (table "
                "model_artifacts). How each scored in testing is at /v1/model-results.",
            }

        return cached(request, ("models",), compute)

    @app.get(
        "/v1/predict/segments",
        response_model=schemas.SegmentList,
        tags=["predictions"],
        dependencies=protected,
        responses=ERRORS,
    )
    def predict_segments(request: Request) -> dict:
        """The segments the live endpoints can predict for (a specialty, an age band, a gender)."""
        predictor = predictor_of(request)

        def compute() -> dict:
            listed = predictor.segments()
            return {
                "count": len(listed),
                "segments": listed,
                "note": "The segments with a model input at the last month of data.",
            }

        return cached(request, ("segments",), compute)

    @app.get(
        "/v1/predict/segment",
        response_model=schemas.SegmentPrediction,
        tags=["predictions"],
        dependencies=protected,
        responses={**ERRORS, 404: {"model": schemas.Problem, "description": "No such segment."}},
    )
    def predict_segment(
        request: Request,
        specialty: Annotated[str, Query(max_length=80, description="For example 'RHEUMATOLOGY'.")],
        age_band: Annotated[str, Query(max_length=40, description="For example '65 TO 74'.")],
        gender: Annotated[str, Query(max_length=20, description="'FEMALE' or 'MALE'.")],
        horizon: Annotated[int, Query(ge=1, le=12, description="Months ahead: 1, 3 or 6.")] = 1,
    ) -> dict:
        """Run the adopted model for one segment now, and compare with the stored forecast."""
        predictor = predictor_of(request)

        def compute() -> dict:
            try:
                out = predictor.segment(specialty, age_band, gender, horizon)
            except LookupError as error:
                raise _not_found(error) from error
            stored = _stored(request, out["specialty"], out["age_band"], out["gender"], horizon)
            if stored is not None:
                out["stored_forecast_pct"] = round(100 * float(stored["point_share"]), 4)
                out["matches_stored"] = abs(out["forecast_pct"] - out["stored_forecast_pct"]) < 1e-3
            return {**out, "note": SEGMENT_NOTE}

        names = (specialty.strip().upper(), age_band.strip().upper(), gender.strip().upper())
        return cached(request, ("segment", *names, horizon), compute)

    @app.get(
        "/v1/predict/alert",
        response_model=schemas.AlertPrediction,
        tags=["predictions"],
        dependencies=protected,
        responses={**ERRORS, 404: {"model": schemas.Problem, "description": "No such segment."}},
    )
    def predict_alert(
        request: Request,
        specialty: Annotated[str, Query(max_length=80)],
        age_band: Annotated[str, Query(max_length=40)],
        gender: Annotated[str, Query(max_length=20)],
    ) -> dict:
        """Run the 1-month decline alert for one segment now, and compare with the stored score."""
        predictor = predictor_of(request)

        def compute() -> dict:
            try:
                out = predictor.alert(specialty, age_band, gender)
            except LookupError as error:
                raise _not_found(error) from error
            stored = _stored(request, out["specialty"], out["age_band"], out["gender"], 1)
            if stored is not None and out["eligible"] and not pd.isna(stored["alert_score"]):
                out["stored_alert_score"] = round(float(stored["alert_score"]), 6)
                out["matches_stored"] = bool(
                    abs(out["alert_score"] - out["stored_alert_score"]) < 1e-6
                    and out["flagged"] == bool(stored["alert_flag"])
                )
            return {**out, "note": ALERT_NOTE}

        names = (specialty.strip().upper(), age_band.strip().upper(), gender.strip().upper())
        return cached(request, ("alert", *names), compute)

    return app


def main() -> None:
    """Run the service (`oa-api`); HOST and PORT come from the environment."""
    import uvicorn

    uvicorn.run(
        "oa_market_intelligence.api.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),  # a container must listen on every interface
        port=int(os.environ.get("PORT", "8000")),
        access_log=False,  # the app writes its own JSON access line
    )


if __name__ == "__main__":
    main()
