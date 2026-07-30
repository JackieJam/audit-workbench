"""Traceable source-asset inventory and immutable file retrieval."""

from __future__ import annotations

from urllib.parse import quote

from audit_engine.source_assets import SourceAsset, read_source_asset
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from audit_api.deps import get_store
from audit_api.routers.analysis import _manifest_or_404

router = APIRouter(prefix="/projects", tags=["sources"])


def _source_assets(manifest) -> list[dict]:
    return [
        {
            **dict(asset),
            "source_type": source.type,
            "source_years": list(source.years),
            "active": bool(asset.get("active", True)),
        }
        for source in manifest.sources
        for asset in source.assets
        if isinstance(asset, dict)
    ]


@router.get("/{project_id}/sources")
def list_sources(
    project_id: str,
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    return {
        "project_id": project_id,
        "data_version": store.current_data_version(project_id),
        "sources": [source.to_dict() for source in manifest.sources],
        "assets": _source_assets(manifest),
    }


@router.get("/{project_id}/sources/{asset_id}/download")
def download_source(
    project_id: str,
    asset_id: str,
    store: ProjectStore = Depends(get_store),
) -> Response:
    manifest = _manifest_or_404(store, project_id)
    raw = next(
        (asset for asset in _source_assets(manifest) if asset.get("asset_id") == asset_id),
        None,
    )
    if raw is None:
        raise HTTPException(status_code=404, detail=f"来源资产不存在: {asset_id}")
    asset = SourceAsset.from_dict(raw)
    try:
        data = read_source_asset(store.project_dir(project_id), asset)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"来源资产完整性校验失败: {exc}") from exc
    encoded = quote(asset.original_name or f"{asset.asset_id}.xlsx", safe="")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="source.xlsx"; filename*=UTF-8\'\'{encoded}'
            ),
            "X-Source-Sha256": asset.sha256,
        },
    )
