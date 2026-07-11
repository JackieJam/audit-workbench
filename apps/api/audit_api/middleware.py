"""HTTP middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response


async def reject_foreign_stream(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Other local apps (e.g. roundtable SSE) may probe /stream on whatever port is open."""
    if request.url.path == "/stream":
        return JSONResponse(
            status_code=404,
            content={
                "detail": "audit-workbench-api",
                "hint": "本服务为审计工作台 API，不提供 /stream SSE。请检查是否有其他工具误连此端口。",
            },
        )
    return await call_next(request)
