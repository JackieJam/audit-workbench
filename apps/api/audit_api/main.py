from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from audit_api.logging_config import configure_access_logging
from audit_api.middleware import reject_foreign_stream
from audit_api.routers import agent, analysis, analysis_modules, candidates, health, ingest, llm_config, llm_insight, pipeline, projects


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_access_logging()
    yield


app = FastAPI(
    title="Audit Workbench API",
    version="0.1.0",
    description="序时账审计分析工作台 — 本地 FastAPI 引擎",
    lifespan=lifespan,
)

app.middleware("http")(reject_foreign_stream)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5188",
        "http://127.0.0.1:5188",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(ingest.router)
app.include_router(analysis.router)
app.include_router(analysis_modules.router)
app.include_router(candidates.router)
app.include_router(llm_insight.router)
app.include_router(pipeline.router)
app.include_router(agent.router)
app.include_router(llm_config.router)

# 确保 reload 子进程在首批请求前即安装 access log 过滤器
configure_access_logging()
