import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type AnalysisQualityResponse,
  type ClassificationDecision,
  type ProjectSummary,
} from "@/api/client";

type Props = {
  project: ProjectSummary;
  quality: AnalysisQualityResponse;
};

function formatAmount(value: number) {
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(1)} 万`;
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function historicalDecisionLabel(
  decision: "map" | "exclude" | "defer" | null | undefined,
  category?: string | null,
) {
  if (decision === "map") return `映射到“${category || "未指定"}”`;
  if (decision === "exclude") return "确认排除";
  if (decision === "defer") return "暂缓决策";
  return "历史决策";
}

export function DataQualityReview({ project, quality }: Props) {
  const queryClient = useQueryClient();
  const [year, setYear] = useState(project.years.at(-1) ?? 0);
  const [drafts, setDrafts] = useState<Record<string, ClassificationDecision>>({});

  useEffect(() => {
    if (!project.years.includes(year)) setYear(project.years.at(-1) ?? 0);
  }, [project.years, year]);

  const yearQuality = quality.years[String(year)];
  const accounts = yearQuality?.review_accounts ?? [];
  const reviewRatio =
    yearQuality?.review_required_amount_ratio ?? yearQuality?.unclassified_amount_ratio ?? 0;
  const excludedRatio = yearQuality?.excluded_amount_ratio ?? 0;
  const correctedCount = accounts.filter((item) => item.system_corrected).length;
  const pending = Object.values(drafts);
  const existingDecisions = quality.classification_decisions ?? [];
  const allowedCategories = quality.allowed_categories ?? [];
  const existingByCode = useMemo(
    () => new Map(existingDecisions.map((item) => [item.account_code, item])),
    [existingDecisions],
  );

  const mutation = useMutation({
    mutationFn: (decisions: ClassificationDecision[]) =>
      api.applyAnalysisQualityDecisions(project.project_id, decisions),
    onSuccess: async (next) => {
      setDrafts({});
      queryClient.setQueriesData(
        {
          predicate: (query) =>
            query.queryKey[0] === "analysis-quality" &&
            query.queryKey.includes(project.project_id),
        },
        next,
      );
      await queryClient.invalidateQueries({
        predicate: (query) => query.queryKey.includes(project.project_id),
      });
    },
  });

  const setDecision = (
    account: { account_code: string; account_name: string },
    decision: ClassificationDecision["decision"],
    category?: string,
  ) => {
    setDrafts((current) => ({
      ...current,
      [account.account_code]: {
        account_code: account.account_code,
        account_name: account.account_name,
        decision,
        category: category || null,
        rationale:
          decision === "map"
            ? `用户确认归入${category}`
            : decision === "exclude"
              ? "用户确认不纳入通用财务画像分类"
              : decision === "defer"
                ? "暂缓，待补充业务资料"
                : "撤销原分类决策",
      },
    }));
  };

  const apply = () => {
    if (!pending.length) return;
    const confirmed = window.confirm(
      "应用分类决策后，将清除旧画像、规则命中、抽样结果、疑点候选和 AI 洞察，并按新口径重新计算。原始序时账不会被修改。是否继续？",
    );
    if (confirmed) mutation.mutate(pending);
  };

  return (
    <div className="quality-review">
      <div className="quality-review__overview">
        <div>
          <strong>口径复核决策台</strong>
          <p>金额分母均为序时账绝对发生额；系统排除不等于数据缺失。</p>
        </div>
        <div className="year-segmented" aria-label="复核年度">
          {project.years.map((item) => (
            <button
              key={item}
              type="button"
              className={year === item ? "active" : ""}
              onClick={() => setYear(item)}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      {yearQuality ? (
        <div className="quality-review__metrics">
          <span>
            <strong>{(reviewRatio * 100).toFixed(1)}%</strong>
            待用户决策
          </span>
          <span>
            <strong>{(excludedRatio * 100).toFixed(1)}%</strong>
            明确排除
          </span>
          <span>
            <strong>{accounts.filter((item) => item.reason === "needs_mapping").length}</strong>
            待分类科目
          </span>
          <span>
            <strong>{correctedCount}</strong>
            已自动纠正
          </span>
        </div>
      ) : null}

      <div className="quality-review__table" role="table">
        {accounts.slice(0, 30).map((account) => {
          const selected = drafts[account.account_code];
          const existing = existingByCode.get(account.account_code);
          return (
            <div className="quality-review__row" key={`${year}-${account.account_code}`}>
              <div className="quality-review__account">
                <span className={`quality-reason quality-reason--${account.reason}`}>
                  {account.reason_label}
                </span>
                <strong>{account.account_code || "无科目编号"}</strong>
                <span>{account.account_name || "无科目名称"}</span>
                <small>{account.row_count.toLocaleString()} 行 · {formatAmount(account.amount)}</small>
                {account.system_corrected ? (
                  <small>
                    历史{historicalDecisionLabel(account.decision, account.decision_category)}
                    未再生效，系统按“{account.effective_category}”分析
                  </small>
                ) : null}
              </div>
              <div className="quality-review__decision">
                {account.mapping_allowed ? (
                  <select
                    aria-label={`${account.account_code}目标分类`}
                    value={selected?.decision === "map" ? selected.category ?? "" : ""}
                    onChange={(event) => {
                      if (event.target.value) setDecision(account, "map", event.target.value);
                    }}
                  >
                    <option value="">选择归入分类…</option>
                    {allowedCategories.map((category) => (
                      <option value={category} key={category}>{category}</option>
                    ))}
                  </select>
                ) : (
                  <span className="quality-review__locked">
                    {account.system_corrected ? "已采用系统高置信度口径" : "通用图表不接收此口径"}
                  </span>
                )}
                {!account.system_corrected ? (
                  <>
                    <button
                      type="button"
                      className={selected?.decision === "exclude" ? "btn-ghost active" : "btn-ghost"}
                      onClick={() => setDecision(account, "exclude")}
                    >
                      确认排除
                    </button>
                    <button
                      type="button"
                      className={selected?.decision === "defer" ? "btn-ghost active" : "btn-ghost"}
                      onClick={() => setDecision(account, "defer")}
                    >
                      暂缓
                    </button>
                  </>
                ) : null}
                {existing ? (
                  <button type="button" className="btn-ghost" onClick={() => setDecision(account, "reset")}>
                    撤销原决策
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
        {!accounts.length ? <p className="quality-review__empty">当前年度没有待复核或已纠正科目。</p> : null}
      </div>

      {existingDecisions.some((item) => item.decision === "map") ? (
        <details className="quality-review__history">
          <summary>查看已映射、当前不在待复核表中的科目</summary>
          {existingDecisions
            .filter((item) => item.decision === "map")
            .map((item) => (
              <div key={item.account_code}>
                <span>{item.account_code} {item.account_name}</span>
                <span>→ {item.category}</span>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => setDecision({
                    account_code: item.account_code,
                    account_name: item.account_name ?? "",
                  }, "reset")}
                >
                  撤销
                </button>
              </div>
            ))}
        </details>
      ) : null}

      <div className="quality-review__footer">
        <span>
          {pending.length
            ? `待应用 ${pending.length} 项；将批量失效一次并重算`
            : correctedCount
              ? "高置信度系统口径已自动生效，无需用户操作"
              : "选择分类、确认排除或暂缓后，再统一应用"}
        </span>
        {mutation.isError ? <span className="quality-review__error">{String(mutation.error)}</span> : null}
        <button
          type="button"
          className="btn-primary"
          disabled={!pending.length || mutation.isPending}
          onClick={apply}
        >
          {mutation.isPending ? "正在重算…" : "应用决策并重新计算"}
        </button>
      </div>
    </div>
  );
}
