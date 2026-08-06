"""仓库 / 打包环境下的路径解析。"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """返回含 `config/` 的根目录。

    优先级：
    1. ``AUDIT_WORKBENCH_CONFIG_ROOT``（指向含 config 的根，或直接指向 config 的父目录）
    2. PyInstaller 解压目录 / 可执行文件旁
    3. 源码布局下的仓库根
    """
    env = (os.environ.get("AUDIT_WORKBENCH_CONFIG_ROOT") or "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "config").is_dir():
            return p
        if p.name == "config" and p.is_dir():
            return p.parent
        return p

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            root = Path(meipass)
            if (root / "config").is_dir():
                return root
        beside = Path(sys.executable).resolve().parent
        if (beside / "config").is_dir():
            return beside
        # macOS .app: Contents/MacOS → Resources
        resources = beside.parent / "Resources"
        if (resources / "config").is_dir():
            return resources

    # packages/engine/audit_engine/paths.py → repo root
    return Path(__file__).resolve().parents[3]


def config_dir() -> Path:
    return repo_root() / "config"
