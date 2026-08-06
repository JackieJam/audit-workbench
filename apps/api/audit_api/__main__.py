"""打包 / 桌面 sidecar 入口：无 reload 启动 uvicorn。"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("AUDIT_API_HOST", "127.0.0.1")
    port = int(os.environ.get("AUDIT_API_PORT", "29180"))
    # LLM 直连厂商，不走本机代理
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(key, None)
    os.environ.setdefault("NO_PROXY", "*")

    uvicorn.run(
        "audit_api.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("AUDIT_API_LOG_LEVEL", "info"),
        access_log=True,
    )


if __name__ == "__main__":
    main()
