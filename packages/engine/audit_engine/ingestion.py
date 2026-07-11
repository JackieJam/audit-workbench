"""
数据摄入模块：加载单/多个 Excel 文件，自动识别年份，输出统一 DataFrame。

支持：
- 单文件单年
- 单文件多年（按 过账日期 拆分）
- 多文件（每文件任意年份，自动合并去重）
- SAP Period 13 归入当年，标记 _is_period13
- 列名映射：用户在 UI 中确认列映射，缺失列以空占位放行（不阻塞下游分析）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal

import pandas as pd
from rapidfuzz import fuzz

# ── 标准字段定义（三档分级 + 模糊匹配候选）────────────────────
# 每个标准列名对应一组候选（首项就是标准名本身）。匹配时不区分大小写、
# 去除空格/标点，按出现顺序优先级递减。
Tier = Literal["core", "important", "auxiliary"]


@dataclass(frozen=True)
class StandardColumn:
    name: str
    tier: Tier
    aliases: tuple[str, ...] = ()
    description: str = ""


STANDARD_COLUMNS: tuple[StandardColumn, ...] = (
    # ── 核心：缺失则无法做基础校验 ──
    StandardColumn("凭证编号", "core", ("凭证号", "凭证 id", "doc no", "document number"),
                   "凭证唯一标识"),
    StandardColumn("过账日期", "core", ("过帐日期", "记账日期", "posting date"),
                   "记账日期，年份识别依赖此列"),
    StandardColumn("凭证货币价值", "core",
                   ("以凭证货币计的金额", "凭证金额", "金额", "amount"),
                   "凭证货币金额（DMBTR）。如果文件只有'本币金额'一列，请映射到'公司代码货币价值'。"),
    StandardColumn("借/贷标识", "core", ("借贷标识", "借贷", "dc indicator"),
                   "S=借方，H=贷方；缺失时尝试从借/贷方列或正负号推断"),

    # ── 重要：缺失只影响相关分析维度 ──
    StandardColumn("总账科目", "important", ("科目编码", "科目代码", "account"),
                   "科目编码，影响科目维度的全部分析"),
    StandardColumn("凭证类型", "important", ("凭证种类", "doc type"),
                   "SA / SK 等手工凭证识别"),
    StandardColumn("供应商编号", "important", ("供应商", "vendor", "vendor code"),
                   "供应商集中度、应付分析"),
    StandardColumn("客户", "important", ("客户编号", "customer", "customer code"),
                   "客户集中度、收入分析"),
    StandardColumn("用户名", "important", ("用户", "操作员", "录入人", "user"),
                   "用户集中度、职责分离分析"),
    StandardColumn("公司代码货币价值", "important",
                   ("本币金额", "本位币金额", "公司代码金额", "总帐金额", "局部货币金额"),
                   "公司本位币金额（HWAER）。多数 SAP 导出叫'本币金额'。"),
    StandardColumn("过账期间", "important", ("过帐期间", "期间", "period"),
                   "SAP 期间，13=年末调整期"),
    StandardColumn("会计年度", "important",
                   ("财年", "fiscal year", "GJAHR"),
                   "SAP 会计年度（GJAHR）。存在时优先用于归集跨年调账，否则回退到过账日期年份。"),
    StandardColumn("文本", "important", ("摘要", "凭证摘要", "line text"),
                   "行项目摘要，关键词分析依赖此列"),

    # ── 辅助：仅影响展示丰富度 ──
    StandardColumn("总账科目：长文本", "auxiliary",
                   ("总账科目长文本", "总账科目：短文本", "总账科目短文本", "科目名称"),
                   "科目中文名"),
    StandardColumn("凭证抬头摘要", "auxiliary", ("抬头文本", "抬头摘要"),
                   "凭证抬头说明"),
    StandardColumn("供应商科目：名称 1", "auxiliary", ("供应商名称",),
                   "供应商中文名"),
    StandardColumn("客户科目：姓名 1", "auxiliary", ("客户名称",),
                   "客户中文名"),
    StandardColumn("物料", "auxiliary", ("物料编号",), "物料编码"),
    StandardColumn("物料：描述", "auxiliary", ("物料描述",), "物料中文名"),
    StandardColumn("物料组", "auxiliary", (), "物料分组"),
    StandardColumn("物料组描述", "auxiliary", (), "物料分组中文名"),
    StandardColumn("成本中心", "auxiliary", (), "成本中心编码"),
    StandardColumn("成本中心：长文本", "auxiliary", ("成本中心：短文本",), "成本中心中文名"),
    StandardColumn("录入时间", "auxiliary", ("输入时间",), "系统录入时间"),
    StandardColumn("凭证日期", "auxiliary", ("doc date",), "业务凭证日期"),
    StandardColumn("输入日期", "auxiliary", ("录入日期",), "录入日期"),
    StandardColumn("借方金额", "auxiliary", ("借方", "debit"),
                   "用于推断借/贷标识"),
    StandardColumn("贷方金额", "auxiliary", ("贷方", "credit"),
                   "用于推断借/贷标识"),
    StandardColumn("反记账", "auxiliary", ("反记帐", "冲销标识"), "冲销标识"),
)

STANDARD_COLUMNS_BY_NAME: dict[str, StandardColumn] = {c.name: c for c in STANDARD_COLUMNS}

# ── 兼容旧调用：保留 REQUIRED_COLUMNS 名义（仅用于参考，不再硬抛）──
REQUIRED_COLUMNS = [c.name for c in STANDARD_COLUMNS if c.tier == "core" and c.name != "借/贷标识"]

DATE_COLUMNS = ["过账日期", "凭证日期", "输入日期"]
NUMERIC_COLUMNS = ["凭证货币价值", "公司代码货币价值", "集团货币价值"]
CODE_COLUMNS = (
    "凭证编号",
    "总账科目",
    "供应商编号",
    "客户",
    "用户名",
    "物料",
    "成本中心",
    "公司代码",
)

# 这些列在下游 add_analysis_columns 中作为"如果存在就优先用"的回退源。
# 缺失时绝不能补 0 占位，否则会把"占位列"误当作真实金额来源。
FALLBACK_ALTERNATE_COLUMNS = {
    "公司代码货币价值",
    "集团货币价值",
    "借方金额",
    "贷方金额",
    "总账科目：长文本",  # 与"总账科目：短文本"互为回退
}

NO_COLUMN_SENTINEL = "(无此列)"


# ─────────────────────────────────────────────
# 公开 API
# ─────────────────────────────────────────────


@dataclass(frozen=True)
class ColumnMatch:
    """单个标准列的匹配结果，带置信度与命中方式，供 UI 标记低置信项。"""
    source: str                                           # 命中的源列名
    score: float                                          # 0~1 置信度
    method: str                                           # exact | alias | contains | fuzzy


@dataclass
class DetectionResult:
    """探测阶段的结果，用于驱动 UI 映射。"""
    source_columns: list[str]                             # 源文件实际列
    suggested_mapping: dict[str, str]                     # 标准列 -> 源列（无匹配则不在 dict 内）
    file_label: str = ""                                  # 用于 UI 显示
    sample: pd.DataFrame = field(default_factory=pd.DataFrame)
    mapping_matches: dict[str, ColumnMatch] = field(default_factory=dict)  # 标准列 -> 匹配详情（含置信度）


def detect_columns(
    sources: list[str | Path | IO],
    learned_aliases: dict[str, str] | None = None,
) -> DetectionResult:
    """读取文件表头并给出建议映射。

    多文件场景下会以「并集」形式展示所有源列，建议映射按"任一文件出现即采纳"。
    后续 load_files() 用同一份 mapping 处理所有文件。

    learned_aliases: {归一化源列名 -> 标准列名}，由调用方从经验库取得后注入。
    ingestion 保持纯函数、不直接依赖 knowledge_base，便于离线测试。
    """
    all_source_cols: list[str] = []
    sample_frames: list[pd.DataFrame] = []
    labels: list[str] = []

    for src in sources:
        sample = _read_header_only(src)
        sample_frames.append(sample)
        for col in sample.columns:
            if col not in all_source_cols:
                all_source_cols.append(col)
        labels.append(_source_label(src))

    matches = suggest_mapping_with_confidence(all_source_cols, learned=learned_aliases)
    suggested = {std: m.source for std, m in matches.items()}
    sample_df = sample_frames[0] if sample_frames else pd.DataFrame()

    return DetectionResult(
        source_columns=all_source_cols,
        suggested_mapping=suggested,
        file_label=" / ".join(labels),
        sample=sample_df,
        mapping_matches=matches,
    )


def load_files(
    sources: list[str | Path | IO],
    column_mapping: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, dict[int, pd.DataFrame], list[str]]:
    """加载文件并应用列映射。

    Args:
        sources: 文件路径或文件流列表。
        column_mapping: 标准列名 -> 源列名；值为 None / NO_COLUMN_SENTINEL 表示用户确认无此列。
                        传 None 时使用自动检测的 suggested_mapping。

    Returns:
        df_unified: 合并后的全部数据（含 _year, _is_period13 列）。
        year_map: {year: df_for_that_year}
        missing_columns: 加载完成后仍缺失的标准列（用于下游分析降级提示）。
    """
    if column_mapping is None:
        detection = detect_columns(sources)
        column_mapping = detection.suggested_mapping

    frames: list[pd.DataFrame] = []
    for src in sources:
        df = _read_single_file(src, column_mapping)
        frames.append(df)

    if len(frames) > 1:
        df_all = pd.concat(frames, ignore_index=True)
        df_all = _deduplicate(df_all)
    else:
        # 单文件场景：相信用户给的就是事实，不做去重
        df_all = frames[0].copy()
    df_all, missing = _post_process(df_all, column_mapping)
    df_all = _tag_years(df_all)

    year_map = {
        year: df_all[df_all["_year"] == year].copy()
        for year in sorted(df_all["_year"].dropna().unique())
    }
    return df_all, year_map, missing


def summarize_years(df_unified: pd.DataFrame) -> list[dict]:
    """返回各年份摘要，供 UI 确认展示。"""
    rows = []
    for year, grp in df_unified.groupby("_year"):
        rows.append({
            "年份": int(year),
            "行数": len(grp),
            "凭证数": grp["凭证编号"].nunique() if "凭证编号" in grp.columns else 0,
            "Period13行数": int(grp["_is_period13"].sum()),
            "金额合计": grp["凭证货币价值"].abs().sum() if "凭证货币价值" in grp.columns else 0,
            "日期范围": f"{grp['过账日期'].min().date()} ~ {grp['过账日期'].max().date()}",
        })
    return rows


# ─────────────────────────────────────────────
# 内部实现
# ─────────────────────────────────────────────


# ── 分层匹配阈值 ────────────────────────────────────────────
# 维护负担下沉到智能层：别名表只需留最常见变体，长尾交给子串/模糊匹配。
# 阈值保守，宁可漏匹配让人工补，也不能把核心金额列误映射（可审计 / 风险可见）。
_LEARNED_SCORE = 0.97        # 学习库命中：用户曾确认过，仅次于标准名精确(1.0)，高于别名
_ALIAS_SCORE = 0.95          # 别名精确命中：高但低于标准名精确，提示这是别名
_CONTAIN_BASE = 0.80         # 子串包含基准分（再按长度比例加成到 0.80~0.95）
_FUZZY_ACCEPT = 0.80         # 模糊命中可接受的最低分（rapidfuzz ratio / 100）
_MIN_CONTAIN_LEN = 2         # 子串命中要求较短侧 >= 2 字，避免单字误命中

# 标准列声明顺序：并列分时让核心列（声明在前）优先认领源列。
_STD_ORDER: dict[str, int] = {c.name: i for i, c in enumerate(STANDARD_COLUMNS)}


def _normalize(text: str) -> str:
    """模糊匹配用：去空白 / 标点 / 大小写。"""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _score_candidate(std: StandardColumn, src_norm: str) -> tuple[float, str]:
    """给单个标准列对单个（已归一化的）源列打分，返回 (分数, 命中方式)。

    分层：标准名精确(1.0) > 别名精确(0.95) > 子串包含(0.80~0.95) > 模糊(rapidfuzz)。
    """
    best_score = 0.0
    best_method = "fuzzy"
    candidates = (std.name, *std.aliases)
    for idx, cand in enumerate(candidates):
        cand_norm = _normalize(cand)
        if not cand_norm:
            continue
        # 精确命中（标准名 / 别名）直接返回，无需再比
        if src_norm == cand_norm:
            return (1.0, "exact") if idx == 0 else (_ALIAS_SCORE, "alias")
        # 子串包含：较短侧 >= 2 字，按长度比例给 0.80~0.95
        shorter, longer = sorted((src_norm, cand_norm), key=len)
        if len(shorter) >= _MIN_CONTAIN_LEN and shorter in longer:
            score = _CONTAIN_BASE + 0.15 * (len(shorter) / len(longer))
            if score > best_score:
                best_score, best_method = score, "contains"
        # 模糊：字符级编辑距离相似度
        ratio = fuzz.ratio(src_norm, cand_norm) / 100.0
        if ratio > best_score:
            best_score, best_method = ratio, "fuzzy"
    return best_score, best_method


def suggest_mapping_with_confidence(
    source_columns: list[str],
    learned: dict[str, str] | None = None,
) -> dict[str, ColumnMatch]:
    """对每个标准列在 source_columns 中找最佳匹配，带置信度与冲突消解。

    Args:
        source_columns: 源文件实际列名。
        learned: {归一化源列名 -> 标准列名}，来自经验库的历史确认（高置信层）。
                 标准名精确命中(1.0)仍优先于学习项(0.97)，保证真实标准列不被错误学习项抢走。

    冲突消解：一个源列最多被一个标准列认领；按分数降序贪心分配，
    并列分时核心列（声明在前）优先，保证结果确定且可复现。
    """
    learned = learned or {}
    norm_source = {col: _normalize(col) for col in source_columns}

    claims: list[tuple[float, int, str, str, str]] = []  # (score, std_order, std_name, src, method)
    for std in STANDARD_COLUMNS:
        for src in source_columns:
            score, method = _score_candidate(std, norm_source[src])
            if score >= _FUZZY_ACCEPT:
                claims.append((score, _STD_ORDER[std.name], std.name, src, method))

    # 学习库层：用户历史确认过的映射，作为高置信候选加入竞争
    for src in source_columns:
        std_name = learned.get(norm_source[src])
        if std_name and std_name in STANDARD_COLUMNS_BY_NAME:
            claims.append((_LEARNED_SCORE, _STD_ORDER[std_name], std_name, src, "learned"))

    # 高分优先；并列时按标准列声明顺序，再按源列名，保证确定性
    claims.sort(key=lambda c: (-c[0], c[1], c[3]))

    used_std: set[str] = set()
    used_src: set[str] = set()
    result: dict[str, ColumnMatch] = {}
    for score, _order, std_name, src, method in claims:
        if std_name in used_std or src in used_src:
            continue
        result[std_name] = ColumnMatch(source=src, score=round(score, 4), method=method)
        used_std.add(std_name)
        used_src.add(src)
    return result


def _suggest_mapping(source_columns: list[str]) -> dict[str, str]:
    """向后兼容：仅返回 标准列 -> 源列 名称映射（丢弃置信度）。"""
    return {std: m.source for std, m in suggest_mapping_with_confidence(source_columns).items()}


def _source_label(src) -> str:
    if hasattr(src, "name"):
        return Path(str(src.name)).name
    return Path(str(src)).name


def _read_header_only(src) -> pd.DataFrame:
    """只读首行用于探测列名。"""
    try:
        return pd.read_excel(src, engine="openpyxl", nrows=5)
    finally:
        # Streamlit UploadedFile 多次读取需要 seek 复位
        if hasattr(src, "seek"):
            try:
                src.seek(0)
            except Exception:
                pass


def _read_single_file(src, mapping: dict[str, str]) -> pd.DataFrame:
    """读取单个文件并应用列映射，返回标准化后的 DataFrame。"""
    df = pd.read_excel(src, engine="openpyxl")
    df = df.dropna(how="all")

    df = _apply_mapping(df, mapping)

    for col in DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    df = _backfill_posting_date(df)

    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in CODE_COLUMNS:
        if col in df.columns:
            df[col] = _normalize_code_column(df[col])

    return df


def _normalize_code_column(series: pd.Series) -> pd.Series:
    """统一编码列为字符串，避免 Parquet 写入时 object 列混入 float。"""
    def one(v: object) -> str:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if isinstance(v, float) and v == int(v):
                return str(int(v))
            return str(v)
        s = str(v).strip()
        if s.endswith(".0") and s[:-2].replace("-", "", 1).isdigit():
            return s[:-2]
        return s

    return series.map(one)


def _backfill_posting_date(df: pd.DataFrame) -> pd.DataFrame:
    """部分导出「过账日期」列为空，但「凭证日期」可用。"""
    if "过账日期" not in df.columns:
        return df
    if df["过账日期"].notna().any():
        if "凭证日期" in df.columns:
            df = df.copy()
            df["过账日期"] = df["过账日期"].fillna(df["凭证日期"])
        return df
    for fallback in ("凭证日期", "输入日期"):
        if fallback in df.columns and df[fallback].notna().any():
            df = df.copy()
            df["过账日期"] = df[fallback]
            return df
    return df


def _apply_mapping(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """按 mapping 把源列重命名为标准列。"""
    rename: dict[str, str] = {}
    for std_name, src_col in mapping.items():
        if not src_col or src_col == NO_COLUMN_SENTINEL:
            continue
        if src_col not in df.columns:
            continue
        if src_col == std_name:
            continue
        # 如果源文件已有同名列，避免重复（保留映射后的列）
        if std_name in df.columns and std_name != src_col:
            df = df.drop(columns=[std_name])
        rename[src_col] = std_name

    if rename:
        df = df.rename(columns=rename)
    return df


def _post_process(
    df: pd.DataFrame, mapping: dict[str, str]
) -> tuple[pd.DataFrame, list[str]]:
    """合成可推断列、补空占位列、统计仍缺失的标准列。"""
    df = df.copy()

    # ── 借/贷标识：三级降级（用户未映射时才尝试合成）──
    if "借/贷标识" not in df.columns:
        df = _synthesize_dc_from_amounts(df)
    if "借/贷标识" not in df.columns:
        df = _synthesize_dc_from_sign(df)

    # ── 凭证货币价值：缺失则尝试从借/贷方金额合成 ──
    if "凭证货币价值" not in df.columns and "借/贷标识" in df.columns:
        df = _synthesize_amount_from_dc(df)

    # ── 收集缺失字段 ──
    missing: list[str] = []
    for std in STANDARD_COLUMNS:
        if std.name in df.columns:
            continue
        # FALLBACK_ALTERNATE_COLUMNS：下游会按"主列 → 回退列"链取数，
        # 不能补占位，否则占位列会被误当作真实数据源。
        if std.name in FALLBACK_ALTERNATE_COLUMNS:
            missing.append(std.name)
            continue
        # 占位列：保证下游 add_analysis_columns / 各分析模块取列时不抛 KeyError
        if std.name in DATE_COLUMNS:
            df[std.name] = pd.NaT
        elif std.name in NUMERIC_COLUMNS or std.name in ("借方金额", "贷方金额"):
            df[std.name] = 0.0
        else:
            df[std.name] = ""
        missing.append(std.name)

    return df, missing


def _synthesize_dc_from_amounts(df: pd.DataFrame) -> pd.DataFrame:
    """从 借方金额 / 贷方金额 推断 借/贷标识（优先级 1）。"""
    debit_col = _find_column(df, ["借方金额", "借方", "Debit", "借方发生额"])
    credit_col = _find_column(df, ["贷方金额", "贷方", "Credit", "贷方发生额"])

    if debit_col is None and credit_col is None:
        return df

    debit_vals = (
        pd.to_numeric(df[debit_col], errors="coerce").fillna(0)
        if debit_col else pd.Series(0, index=df.index)
    )
    credit_vals = (
        pd.to_numeric(df[credit_col], errors="coerce").fillna(0)
        if credit_col else pd.Series(0, index=df.index)
    )

    dc = pd.Series("", index=df.index, dtype=str)
    mask_debit_only = (debit_vals > 0) & (credit_vals == 0)
    mask_credit_only = (credit_vals > 0) & (debit_vals == 0)
    mask_both = (debit_vals > 0) & (credit_vals > 0)

    dc[mask_debit_only] = "S"
    dc[mask_credit_only] = "H"
    dc[mask_both] = pd.Series(
        ["S" if d >= c else "H" for d, c in zip(debit_vals[mask_both], credit_vals[mask_both], strict=True)],
        index=dc[mask_both].index,
    )

    df["借/贷标识"] = dc
    return df


def _synthesize_dc_from_sign(df: pd.DataFrame) -> pd.DataFrame:
    """从 凭证货币价值 正负号推断 借/贷标识（优先级 2）。"""
    if "凭证货币价值" not in df.columns:
        return df

    amount = pd.to_numeric(df["凭证货币价值"], errors="coerce").fillna(0)
    dc = pd.Series("", index=df.index, dtype=str)
    dc[amount > 0] = "S"
    dc[amount < 0] = "H"
    df["借/贷标识"] = dc
    return df


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _synthesize_amount_from_dc(df: pd.DataFrame) -> pd.DataFrame:
    """从 借方金额 / 贷方金额 列合成 凭证货币价值。"""
    debit_col = _find_column(df, ["借方金额", "借方", "Debit", "借方发生额"])
    credit_col = _find_column(df, ["贷方金额", "贷方", "Credit", "贷方发生额"])

    if debit_col is None and credit_col is None:
        return df

    debit_vals = (
        pd.to_numeric(df[debit_col], errors="coerce").fillna(0)
        if debit_col else pd.Series(0, index=df.index)
    )
    credit_vals = (
        pd.to_numeric(df[credit_col], errors="coerce").fillna(0)
        if credit_col else pd.Series(0, index=df.index)
    )

    dc = df["借/贷标识"].astype(str).str.strip()
    df["凭证货币价值"] = 0.0
    df.loc[dc == "S", "凭证货币价值"] = debit_vals[dc == "S"]
    df.loc[dc == "H", "凭证货币价值"] = credit_vals[dc == "H"]
    mask_both = (dc != "S") & (dc != "H")
    df.loc[mask_both, "凭证货币价值"] = (
        debit_vals[mask_both].combine(credit_vals[mask_both], max)
    )
    return df


def _deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """跨文件合并后的去重。

    设计目标：
    - 多文件场景下，若同一行被两个文件都包含（边界期间重叠），合并后去掉一份
    - 单文件场景下，绝对不能因为"伪相似"折叠同一凭证内的多条分录

    策略：仅当**整行所有列值完全一致**时才视为重复。这是最保守的策略，
    宁可保留少量真实重复，也不能因去重错误丢失业务数据。
    """
    if df.empty:
        return df
    before = len(df)
    df = df.drop_duplicates()
    removed = before - len(df)
    if removed > 0:
        import warnings
        warnings.warn(f"去重移除 {removed} 行整行重复记录", stacklevel=2)
    return df


def _tag_years(df: pd.DataFrame) -> pd.DataFrame:
    """打 _year 和 _is_period13 标签。

    年份归集优先级：
    1. 会计年度（GJAHR）：SAP 标准的财年字段，能正确归集跨年调账凭证。
       例如：业务发生在 2025 年但 1/2026 才过账的调整凭证，会计年度=2025。
    2. 过账日期：会计年度缺失或为空时回退到 过账日期.dt.year。

    Period 13 同样从 过账期间 字段识别（年末调整期标识）。
    """
    df = df.copy()

    period13_mask = pd.Series(False, index=df.index)
    if "过账期间" in df.columns:
        period_vals = pd.to_numeric(df["过账期间"], errors="coerce")
        period13_mask = period_vals == 13
    df["_is_period13"] = period13_mask

    # 优先使用会计年度
    fiscal_year = pd.Series(pd.NA, index=df.index, dtype="Int64")
    if "会计年度" in df.columns:
        fiscal_year = pd.to_numeric(df["会计年度"], errors="coerce").astype("Int64")

    # 回退到过账日期
    posting_year = pd.Series(pd.NA, index=df.index, dtype="Int64")
    if "过账日期" in df.columns and df["过账日期"].notna().any():
        posting_year = df["过账日期"].dt.year.astype("Int64")

    df["_year"] = fiscal_year.fillna(posting_year)
    return df
