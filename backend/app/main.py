import logging
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api.artifacts import router as artifacts_router
from app.api.conversations import router as conversations_router
from app.api.cost_data import router as cost_data_router
from app.api.runtime_runs import router as runtime_runs_router
from app.services.health_service import health_snapshot, readiness_snapshot
from app.settings import AppSettings, get_settings

LOGGER = logging.getLogger("app.http")


async def add_request_context(request: Request, call_next):
    started_at = perf_counter()
    supplied = request.headers.get("X-Request-ID", "")
    try:
        request_id = str(UUID(supplied)) if supplied else str(uuid4())
    except ValueError:
        request_id = str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    duration_ms = (perf_counter() - started_at) * 1000
    response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
    response.headers["X-Request-ID"] = request_id
    return response


async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    message = detail.get("message")
    if not isinstance(message, str):
        message = "请求未能完成。"
    body = {
        "code": str(detail.get("code") or "http_error"),
        "message": message,
        "request_id": request.state.request_id,
        "details": detail.get("details")
        if isinstance(detail.get("details"), dict)
        else {},
    }
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": body},
        headers={"X-Request-ID": request.state.request_id},
    )


async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        {"type": item.get("type"), "loc": item.get("loc"), "msg": item.get("msg")}
        for item in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "validation_error",
                "message": "请求参数校验失败。",
                "request_id": request.state.request_id,
                "details": {"errors": errors},
            }
        },
        headers={"X-Request-ID": request.state.request_id},
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    LOGGER.exception(
        "unhandled request error",
        extra={"request_id": request.state.request_id, "path": request.url.path},
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": "服务暂时不可用，请稍后重试。",
                "request_id": request.state.request_id,
                "details": {},
            }
        },
        headers={"X-Request-ID": request.state.request_id},
    )


def create_app(settings: AppSettings | None = None) -> FastAPI:
    configured = settings or get_settings()
    api = FastAPI(
        title="CostGraph",
        version=configured.app_version,
    )
    api.state.settings = configured
    api.add_middleware(GZipMiddleware, minimum_size=1024)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=configured.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.middleware("http")(add_request_context)
    api.add_exception_handler(HTTPException, handle_http_exception)
    api.add_exception_handler(RequestValidationError, handle_validation_error)
    api.add_exception_handler(Exception, handle_unexpected_error)

    @api.get("/api/health")
    def health(request: Request) -> JSONResponse:
        snapshot = health_snapshot(request.app.state.settings)
        return JSONResponse(
            status_code=200 if snapshot["status"] == "ok" else 503,
            content=snapshot,
        )

    @api.get("/api/livez")
    def livez(request: Request) -> dict[str, str]:
        current: AppSettings = request.app.state.settings
        return {
            "status": "alive",
            "service": "costgraph",
            "app_env": current.app_env,
            "app_version": current.app_version,
        }

    @api.get("/api/readyz")
    def readyz(request: Request) -> JSONResponse:
        ready, snapshot = readiness_snapshot(request.app.state.settings)
        return JSONResponse(status_code=200 if ready else 503, content=snapshot)

    api.include_router(artifacts_router, prefix="/api/artifacts", tags=["artifacts"])
    api.include_router(cost_data_router, prefix="/api/cost-data", tags=["cost-data"])
    api.include_router(
        runtime_runs_router, prefix="/api/v2/conversations", tags=["runtime"]
    )
    api.include_router(
        conversations_router,
        prefix="/api/conversations",
        tags=["conversations"],
    )
    return api


app = create_app()
