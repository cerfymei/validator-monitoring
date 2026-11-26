import os
from contextlib import asynccontextmanager
from typing import Any
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

DEVICE_SERVICE_URL = os.getenv("DEVICE_SERVICE_URL", "http://device_registry:8000")
TELEMETRY_SERVICE_URL = os.getenv("TELEMETRY_SERVICE_URL", "http://telemetry_service:8000")
INCIDENT_SERVICE_URL = os.getenv("INCIDENT_SERVICE_URL", "http://incident_analytics:8000")

UPSTREAMS = {
    "device_registry": DEVICE_SERVICE_URL,
    "telemetry_service": TELEMETRY_SERVICE_URL,
    "incident_analytics": INCIDENT_SERVICE_URL,
}

ORDERED_TAGS = [
    {"name": "Транспорт (ТС)", "description": "Контекст 1: учёт автобусов и электробусов"},
    {"name": "Валидаторы", "description": "Контекст 1: жизненный цикл и привязка валидаторов к ТС"},
    {"name": "Телеметрия", "description": "Контекст 2: сбор показаний датчиков и онлайн-статус"},
    {"name": "Инциденты", "description": "Контекст 3: управление поломками и ремонтами"},
    {"name": "Уведомления", "description": "Контекст 3: оповещения сервисных инженеров"},
    {"name": "Аналитика", "description": "Контекст 3: сводные метрики работы сервиса"},
    {"name": "Система", "description": "Проверка доступности микросервисов за шлюзом"},
]

async_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global async_client
    async_client = httpx.AsyncClient(timeout=10.0)
    yield
    await async_client.aclose()


app = FastAPI(
    title="Validator Monitoring — Единый API Gateway",
    description=(
        "Единая точка входа и общая документация Swagger для трёх микросервисов:\n"
        "- **Device Registry** (ТС и валидаторы)\n"
        "- **Telemetry Service** (приём телеметрии и статус)\n"
        "- **Incident & Analytics** (инциденты, уведомления и аналитика)"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_INDEX = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


@app.get("/", include_in_schema=False)
async def serve_frontend():
    return FileResponse(FRONTEND_INDEX)

def resolve_upstream(path: str) -> str | None:
    clean = "/" + path.lstrip("/")
    if clean.startswith("/vehicles") or clean.startswith("/validators"):
        return DEVICE_SERVICE_URL
    if clean.startswith("/telemetry"):
        return TELEMETRY_SERVICE_URL
    if (
        clean.startswith("/incidents")
        or clean.startswith("/notifications")
        or clean.startswith("/analytics")
    ):
        return INCIDENT_SERVICE_URL
    return None


def merge_openapi_specs(base_schema: dict[str, Any], specs: list[dict[str, Any]]) -> dict[str, Any]:
    merged_paths: dict[str, Any] = dict(base_schema.get("paths", {}))
    merged_schemas: dict[str, Any] = dict(base_schema.get("components", {}).get("schemas", {}))

    for spec in specs:
        merged_paths.update(spec.get("paths", {}))
        merged_schemas.update(spec.get("components", {}).get("schemas", {}))

    return {
        "openapi": "3.1.0",
        "info": base_schema["info"],
        "tags": ORDERED_TAGS,
        "paths": merged_paths,
        "components": {"schemas": merged_schemas},
    }


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    from fastapi.openapi.utils import get_openapi

    base_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    fetched_specs: list[dict[str, Any]] = []
    all_loaded = True
    for url in UPSTREAMS.values():
        try:
            resp = httpx.get(f"{url}/openapi.json", timeout=2.0)
            if resp.status_code == 200:
                fetched_specs.append(resp.json())
            else:
                all_loaded = False
        except Exception:
            all_loaded = False

    merged = merge_openapi_specs(base_schema, fetched_specs)
    if all_loaded and len(fetched_specs) == len(UPSTREAMS):
        app.openapi_schema = merged
    return merged


app.openapi = custom_openapi  # type: ignore[method-assign]


@app.get("/health", tags=["Система"], summary="Состояние шлюза и всех микросервисов")
async def gateway_health():
    statuses: dict[str, str] = {}
    assert async_client is not None
    for name, url in UPSTREAMS.items():
        try:
            resp = await async_client.get(f"{url}/openapi.json", timeout=2.0)
            statuses[name] = "ok" if resp.status_code == 200 else f"http_{resp.status_code}"
        except Exception:
            statuses[name] = "unreachable"
    return {"gateway": "ok", "services": statuses}


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def proxy_request(full_path: str, request: Request):
    upstream_base = resolve_upstream(full_path)
    if not upstream_base:
        return JSONResponse(status_code=404, content={"detail": "Маршрут не найден на API Gateway"})

    target_url = f"{upstream_base}/{full_path.lstrip('/')}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    body = await request.body()
    forward_headers = {}
    if "content-type" in request.headers:
        forward_headers["content-type"] = request.headers["content-type"]
    if "accept" in request.headers:
        forward_headers["accept"] = request.headers["accept"]

    assert async_client is not None
    try:
        upstream_resp = await async_client.request(
            method=request.method,
            url=target_url,
            content=body if body else None,
            headers=forward_headers,
        )
    except httpx.RequestError as exc:
        return JSONResponse(
            status_code=502,
            content={"detail": f"Микросервис временно недоступен: {exc}"},
        )

    if upstream_resp.status_code == 204:
        return Response(status_code=204)

    media_type = upstream_resp.headers.get("content-type", "application/json")
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        media_type=media_type,
    )