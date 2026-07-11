#!/usr/bin/env python3
"""真实序时账案例冒烟测试 — 分阶段导入 + 全部分析子模块 + 流水线。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "packages" / "engine"))

from audit_api.deps import get_pipeline, get_store  # noqa: E402
from audit_api.main import app  # noqa: E402
from audit_engine.ingestion import load_files  # noqa: E402
from audit_engine.store import ProjectStore  # noqa: E402

TEST_DATA_DIR = Path("/Users/jackie_m/Downloads/序时账测试案例")
JOURNAL_2022 = TEST_DATA_DIR / "2022年6-12月序时账.XLSX"
JOURNAL_2023 = TEST_DATA_DIR / "1094 810序时账（23.1-23.12）.XLSX"
JOURNAL_2024 = TEST_DATA_DIR / "1094 810序时账（24.1-24.12）.XLSX"
APPEND_FILES = [JOURNAL_2023, JOURNAL_2024]

client = TestClient(app)


def log(msg: str) -> None:
    print(msg, flush=True)


def _ok(label: str, resp) -> dict | list:
    if resp.status_code != 200:
        raise RuntimeError(f"{label} failed ({resp.status_code}): {resp.text[:800]}")
    return resp.json()


def _file_payload(path: Path) -> tuple[str, bytes, str]:
    return (
        path.name,
        path.read_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def main() -> int:
    if not JOURNAL_2022.exists():
        log(f"缺少测试文件: {JOURNAL_2022}")
        return 1

    total_t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="audit-smoke-") as tmp:
        os.environ["AUDIT_WORKBENCH_DATA_ROOT"] = tmp
        get_store.cache_clear()
        get_pipeline.cache_clear()

        t0 = time.perf_counter()
        log("▶ 创建项目")
        pid = _ok("create", client.post("/projects", json={"name": "真实案例冒烟"}))["project_id"]
        log(f"  ✓ {time.perf_counter() - t0:.1f}s  project_id={pid}")

        t0 = time.perf_counter()
        log("▶ 列名检测（2022 样本）")
        det = _ok(
            "detect",
            client.post(
                f"/projects/{pid}/ingest/detect",
                files=[("files", _file_payload(JOURNAL_2022))],
            ),
        )
        mapping = det["suggested_mapping"]
        assert "过账日期" in mapping
        log(f"  映射字段数: {len(mapping)}")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log("▶ 导入 2022 序时账")
        commit = _ok(
            "commit 2022",
            client.post(
                f"/projects/{pid}/ingest/commit",
                files=[("files", _file_payload(JOURNAL_2022))],
                data={"column_mapping": json.dumps(mapping)},
            ),
        )
        years = sorted(commit["years"])
        log(f"  行数: {commit['total_rows']:,}  年份: {years}")
        for row in commit["year_summary"]:
            log(f"    {row['年份']}: {row['行数']:,} 行 / {row['凭证数']:,} 凭证")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        store = ProjectStore(root=Path(tmp))
        for path in APPEND_FILES:
            if not path.exists():
                log(f"  ⚠ 跳过缺失文件: {path.name}")
                continue
            t0 = time.perf_counter()
            log(f"▶ 追加导入 {path.name}")
            _, year_map, _ = load_files([path], column_mapping=mapping)
            for year, df in sorted(year_map.items()):
                n = store.save_journal_year(pid, int(year), df)
                log(f"    {year}: {n:,} 行")
                years = sorted(set(years) | {int(year)})
            log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        latest = max(years)
        prev = sorted(years)[-2] if len(years) >= 2 else latest

        t0 = time.perf_counter()
        log(f"▶ 收入成本（{latest}）")
        monthly = _ok(
            "income monthly",
            client.get(
                f"/projects/{pid}/analysis/income-cost/monthly",
                params={"year": latest, "category": "总计"},
            ),
        )
        rows = monthly["rows"]
        nonzero = sum(1 for r in rows if abs(r.get("净收入", 0)) > 0)
        top_month = max(rows, key=lambda r: abs(r.get("净收入", 0)))
        log(f"  非零净收入月: {nonzero}/12，峰值月 {top_month['月份']} 净收入 {top_month['净收入']:,.0f}")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log("▶ 跨年费用结构")
        expense = _ok("expense", client.get(f"/projects/{pid}/analysis/expense/cross-year"))
        exp_rows = expense.get("rows") or []
        if exp_rows:
            top = max(exp_rows, key=lambda r: abs(r.get("合计", r.get("金额", 0))))
            log(f"  分类数: {len(exp_rows)}，示例: {top.get('费用分类', top)}")
        else:
            log(f"  分类数: 0")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log(f"▶ 暂估往来（{latest}）")
        ap = _ok(
            "ap",
            client.get(
                f"/projects/{pid}/analysis/working-capital/ap-accrual/monthly",
                params={"year": latest},
            ),
        )
        ap_net = sum(abs(r.get("暂估净额", 0)) for r in ap["rows"])
        log(f"  应付暂估月度 {len(ap['rows'])} 行，净额绝对值合计 {ap_net:,.0f}")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log(f"▶ 资产负债（{latest}）")
        cats = _ok(
            "bs",
            client.get(f"/projects/{pid}/analysis/balance-sheet/categories", params={"year": latest}),
        )
        cat_list = cats["categories"]
        log(f"  类别数: {len(cat_list)}")
        if cat_list:
            bs_m = _ok(
                "bs monthly",
                client.get(
                    f"/projects/{pid}/analysis/balance-sheet/monthly",
                    params={"year": latest, "category": cat_list[0]},
                ),
            )
            log(f"  「{cat_list[0]}」月度 {len(bs_m['rows'])} 行")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log(f"▶ 调账冲销（{latest}）")
        adj = _ok(
            "adjustment",
            client.get(f"/projects/{pid}/analysis/adjustment/summary", params={"year": latest}),
        )
        log(f"  摘要行: {len(adj['rows'])}")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log("▶ 统计画像")
        prof = _ok("profiles", client.post(f"/projects/{pid}/pipeline/profiles"))
        log(f"  画像年份: {sorted(prof['profiles'].keys())}")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log("▶ 跨年稽核")
        cross = _ok("cross-year", client.post(f"/projects/{pid}/pipeline/cross-year"))
        findings = cross.get("findings") or []
        log(f"  发现数: {len(findings)}")
        for f in findings[:3]:
            log(f"    · {f.get('title', f.get('type', f))}")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log("▶ 规则执行 + 抽样 + 导出")
        rules = _ok("rules", client.post(f"/projects/{pid}/pipeline/rules/run"))
        hits = rules.get("hits") or rules.get("rule_hits") or []
        samples = _ok(
            "samples",
            client.post(f"/projects/{pid}/pipeline/samples", json={"method": "by_rule", "size": 20}),
        )
        export = client.get(f"/projects/{pid}/pipeline/export")
        if export.status_code != 200:
            raise RuntimeError(f"export failed: {export.status_code}")
        log(f"  规则命中: {len(hits)}  样本: {samples.get('count', len(samples.get('samples', [])))}  Excel: {len(export.content):,} bytes")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        t0 = time.perf_counter()
        log(f"▶ 钻取（{latest} 年有数据的首月）")
        peak = max(rows, key=lambda r: abs(r.get("净收入", 0)))
        drill = _ok(
            "drilldown",
            client.post(
                f"/projects/{pid}/analysis/drilldown",
                json={
                    "selector": {
                        "kind": "monthly_income_cost",
                        "year": latest,
                        "month": peak["月份"],
                        "metric": "revenue",
                        "category": "总计",
                    }
                },
                params={"limit": 20},
            ),
        )
        log(f"  {latest}年{peak['月份']}月钻取 {drill['row_count']} 行")
        log(f"  ✓ {time.perf_counter() - t0:.1f}s")

        get_store.cache_clear()
        get_pipeline.cache_clear()

    log(f"\n{'=' * 50}")
    log(f"全部通过 · 总耗时 {time.perf_counter() - total_t0:.1f}s")
    log(f"年份覆盖: {years}  （{prev} → {latest} 跨年可用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
