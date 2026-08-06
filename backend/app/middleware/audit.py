"""FastAPI middleware — audit every API request (non-blocking)."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.services.audit_dispatch import schedule_audit_event
from app.services.audit_types import AuditComponent, AuditStatus, audit_status_for_http


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not getattr(settings, "audit_log_api_requests", True):
            return await call_next(request)

        if request.url.path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(request)

        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())[:12]
        action = f"api.{request.method.lower()}.{request.url.path.strip('/').replace('/', '.') or 'root'}"
        start = time.perf_counter()
        context = {
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query) if request.url.query else None,
        }

        try:
            response = await call_next(request)
            duration_ms = int((time.perf_counter() - start) * 1000)
            status = audit_status_for_http(response.status_code)
            schedule_audit_event(
                action,
                AuditComponent.API.value,
                status,
                duration_ms=duration_ms,
                message=f"HTTP {response.status_code}",
                context={**context, "status_code": response.status_code},
                request_id=request_id,
            )
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            schedule_audit_event(
                action,
                AuditComponent.API.value,
                AuditStatus.FAILED,
                duration_ms=duration_ms,
                message="Unhandled API exception",
                error=exc,
                context=context,
                request_id=request_id,
            )
            raise
