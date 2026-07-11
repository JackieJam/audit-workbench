from __future__ import annotations

import pandas as pd

from audit_engine.ingestion import _backfill_posting_date, _normalize_code_column


def test_backfill_from_voucher_date() -> None:
    df = pd.DataFrame(
        {
            "过账日期": [pd.NaT, pd.NaT],
            "凭证日期": pd.to_datetime(["2022-06-01", "2022-07-01"]),
        }
    )
    out = _backfill_posting_date(df)
    assert out["过账日期"].notna().all()


def test_normalize_mixed_vendor_code() -> None:
    s = pd.Series(["V001", 1002.0, float("nan"), 3003])
    out = _normalize_code_column(s)
    assert out.tolist() == ["V001", "1002", "", "3003"]
