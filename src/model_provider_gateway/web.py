"""Loopback-only provider onboarding console."""

import secrets
from importlib import resources
from pathlib import Path
from typing import Any

from .mock_server import mock_smoke
from .onboarding import OnboardingService, onboarding_catalog, redact_profile
from .runtime import DEFAULT_UI_HOST, DEFAULT_UI_PORT, HTTP_API_SCHEMA, proxy_plan


def _service_secret_ready(service: OnboardingService, auth_ref: str) -> bool:
    kind, separator, name = auth_ref.partition(":")
    if not separator or not name:
        return False
    if kind == "keyring":
        try:
            return bool(service.secret_store.get(name))
        except RuntimeError:
            return False
    if kind == "env":
        from .secrets import EnvironmentSecretStore

        return bool(EnvironmentSecretStore().get(name))
    return False


def _asset(name: str) -> str:
    package = resources.files("model_provider_gateway.static")
    return package.joinpath(name).read_text(encoding="utf-8")


def create_app(*, state_dir: Path | None = None, service: OnboardingService | None = None):
    try:
        from fastapi import FastAPI, Header, HTTPException, Request
        from fastapi.responses import HTMLResponse, Response
    except ImportError as exc:
        raise RuntimeError("UI support is not installed; install model-provider-gateway[ui]") from exc

    selected_service = service or OnboardingService.system_default(state_dir)
    csrf_token = secrets.token_urlsafe(24)
    app = FastAPI(
        title="Model Provider Gateway",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return _asset("index.html")

    @app.get("/assets/app.js")
    def javascript() -> Response:
        return Response(_asset("app.js"), media_type="application/javascript")

    @app.get("/assets/styles.css")
    def stylesheet() -> Response:
        return Response(_asset("styles.css"), media_type="text/css")

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "status": "control_ui_ready",
            "service": "model-provider-gateway-provider-console",
            "api_schema": HTTP_API_SCHEMA,
            "provider_call_performed": False,
            "proxy_started": False,
        }

    @app.get("/api/v1/bootstrap")
    def bootstrap() -> dict[str, Any]:
        profiles = []
        for profile in selected_service.profile_store.list_profiles():
            row = redact_profile(profile)
            row["auth_ready"] = _service_secret_ready(selected_service, profile["auth_ref"])
            profiles.append(row)
        return {
            "schema": HTTP_API_SCHEMA,
            "csrf_token": csrf_token,
            "catalog": onboarding_catalog(),
            "profiles": profiles,
            "runtime": {
                "ui": "control_ui_ready",
                "proxy": "stopped_by_design",
                "provider_call_performed": False,
            },
        }

    def require_write_token(request: Request, supplied: str | None) -> None:
        if supplied != csrf_token:
            raise HTTPException(status_code=403, detail="write token rejected")
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
            raise HTTPException(status_code=403, detail="cross-site write rejected")

    @app.post("/api/v1/profiles")
    async def save_profile(request: Request, x_mpg_csrf: str | None = Header(default=None)) -> dict[str, Any]:
        require_write_token(request, x_mpg_csrf)
        payload = await request.json()
        try:
            return selected_service.onboard(
                provider_id=str(payload.get("provider_id") or ""),
                preset_id=str(payload["preset_id"]) if payload.get("preset_id") else None,
                profile_id=str(payload["profile_id"]) if payload.get("profile_id") else None,
                api_key=str(payload.get("api_key") or ""),
                provider_fields=dict(payload.get("provider_fields") or {}),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/v1/proxy-plan")
    def runtime_plan() -> dict[str, Any]:
        try:
            return proxy_plan(selected_service.profile_store)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/v1/mock-smoke")
    def local_smoke(request: Request, x_mpg_csrf: str | None = Header(default=None)) -> dict[str, Any]:
        require_write_token(request, x_mpg_csrf)
        result = mock_smoke(timeout_seconds=1.0)
        return {
            "schema": HTTP_API_SCHEMA,
            "ok": result["ok"],
            "status": result["status"],
            "mode": "loopback_mock",
            "provider_call_performed": False,
        }

    return app


def serve_ui(*, host: str = DEFAULT_UI_HOST, port: int = DEFAULT_UI_PORT, state_dir: Path | None = None) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("provider console must bind to a loopback host")
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("UI support is not installed; install model-provider-gateway[ui]") from exc
    uvicorn.run(create_app(state_dir=state_dir), host=host, port=port, log_level="warning")
