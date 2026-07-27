import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type RulesConfig } from "@/api/client";
import {
  COMPLEX_PARAM_KEYS,
  RULE_ORDER,
  paramLabel,
  ruleTitle,
  RULE_META,
} from "@/lib/ruleMeta";

type Props = {
  projectId: string;
};

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function cloneConfig(cfg: RulesConfig): RulesConfig {
  return structuredClone(cfg);
}

function stringifyList(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value == null) return "";
  return String(value);
}

function parseList(raw: string, asNumber: boolean): string[] | number[] {
  const parts = raw
    .split(/[,，\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (asNumber) {
    return parts.map((p) => Number(p)).filter((n) => Number.isFinite(n));
  }
  return parts;
}

function ParamEditor({
  paramKey,
  value,
  onChange,
}: {
  paramKey: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  if (COMPLEX_PARAM_KEYS.has(paramKey) || isPlainObject(value)) {
    return (
      <p className="muted rules-panel__skip">
        {paramLabel(paramKey)}：复杂结构，请用 Agent 调整
      </p>
    );
  }

  if (typeof value === "boolean") {
    return (
      <label className="rules-panel__field">
        <span>{paramLabel(paramKey)}</span>
        <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      </label>
    );
  }

  if (typeof value === "number") {
    return (
      <label className="rules-panel__field">
        <span>{paramLabel(paramKey)}</span>
        <input
          type="number"
          step="any"
          value={Number.isFinite(value) ? value : 0}
          onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        />
      </label>
    );
  }

  if (Array.isArray(value)) {
    const asNumber = value.length > 0 && typeof value[0] === "number";
    return (
      <label className="rules-panel__field rules-panel__field--wide">
        <span>{paramLabel(paramKey)}</span>
        <input
          type="text"
          value={stringifyList(value)}
          onChange={(e) => onChange(parseList(e.target.value, asNumber))}
        />
      </label>
    );
  }

  return (
    <label className="rules-panel__field rules-panel__field--wide">
      <span>{paramLabel(paramKey)}</span>
      <input type="text" value={value == null ? "" : String(value)} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function RuleCard({
  ruleId,
  block,
  onChange,
}: {
  ruleId: string;
  block: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const meta = RULE_META[ruleId];
  const enabled = block.enabled !== false;
  const rationale = typeof block.rationale === "string" ? block.rationale : "";
  const paramKeys = Object.keys(block).filter((k) => k !== "enabled" && k !== "rationale");

  return (
    <article className={`rules-card${enabled ? "" : " is-disabled"}`}>
      <header className="rules-card__header">
        <div>
          <h4>{ruleTitle(ruleId)}</h4>
          {meta?.purpose && <p className="muted">{meta.purpose}</p>}
        </div>
        {"enabled" in block && (
          <label className="rules-card__toggle">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => onChange({ ...block, enabled: e.target.checked })}
            />
            {enabled ? "启用" : "关闭"}
          </label>
        )}
      </header>

      <div className="rules-card__params">
        {paramKeys.map((key) => (
          <ParamEditor
            key={key}
            paramKey={key}
            value={block[key]}
            onChange={(next) => onChange({ ...block, [key]: next })}
          />
        ))}
      </div>

      {rationale && <p className="rules-card__rationale">{rationale}</p>}
    </article>
  );
}

export function RulesPanel({ projectId }: Props) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<RulesConfig | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const rulesQuery = useQuery({
    queryKey: ["rules-config", projectId],
    queryFn: () => api.getRules(projectId),
  });

  useEffect(() => {
    if (rulesQuery.data) {
      setDraft(cloneConfig(rulesQuery.data));
      setMessage(null);
    }
  }, [rulesQuery.data]);

  const dirty = useMemo(() => {
    if (!draft || !rulesQuery.data) return false;
    return JSON.stringify(draft) !== JSON.stringify(rulesQuery.data);
  }, [draft, rulesQuery.data]);

  const save = useMutation({
    mutationFn: (body: RulesConfig) => api.putRules(projectId, body),
    onSuccess: (saved) => {
      setDraft(cloneConfig(saved));
      queryClient.setQueryData(["rules-config", projectId], saved);
      queryClient.invalidateQueries({ queryKey: ["rule-results", projectId] });
      queryClient.invalidateQueries({ queryKey: ["samples", projectId] });
      queryClient.invalidateQueries({ queryKey: ["cross-year"] });
      setMessage("已保存。规则命中与样本已清空，请重新运行规则引擎。");
    },
    onError: (err) => setMessage(`保存失败：${String(err)}`),
  });

  const resetDefaults = useMutation({
    mutationFn: () => api.getDefaultRules(projectId),
    onSuccess: (defaults) => {
      setDraft(cloneConfig(defaults));
      setMessage("已载入默认规则（尚未保存）。");
    },
    onError: (err) => setMessage(`载入默认失败：${String(err)}`),
  });

  if (rulesQuery.isLoading) {
    return <div className="rules-panel muted">加载规则配置…</div>;
  }
  if (rulesQuery.isError || !draft) {
    return <div className="rules-panel error">规则配置加载失败</div>;
  }

  const ruleIds = [
    ...RULE_ORDER.filter((id) => isPlainObject(draft[id])),
    ...Object.keys(draft).filter(
      (id) =>
        isPlainObject(draft[id]) &&
        !RULE_ORDER.includes(id as (typeof RULE_ORDER)[number]) &&
        id !== "routine_exclusion",
    ),
  ];

  const maxSample =
    typeof draft.max_sample_size === "number" ? draft.max_sample_size : 50;
  const whitelistKeywords = Array.isArray(draft.whitelist_keywords)
    ? draft.whitelist_keywords
    : [];
  const whitelistTypes = Array.isArray(draft.whitelist_voucher_types)
    ? draft.whitelist_voucher_types
    : [];

  return (
    <div className="rules-panel">
      <div className="rules-panel__toolbar">
        <div>
          <h3>规则配置</h3>
          <p className="muted">启停规则、调整阈值；保存后需重新运行规则引擎才会反映到命中结果。</p>
        </div>
        <div className="rules-panel__actions">
          <button
            type="button"
            className="btn-ghost"
            disabled={resetDefaults.isPending || save.isPending}
            onClick={() => resetDefaults.mutate()}
          >
            恢复默认
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!dirty || save.isPending}
            onClick={() => draft && save.mutate(draft)}
          >
            {save.isPending ? "保存中…" : dirty ? "保存规则" : "已保存"}
          </button>
        </div>
      </div>

      {message && <p className={message.startsWith("保存失败") ? "error" : "muted"}>{message}</p>}

      <div className="rules-card rules-card--global">
        <h4>全局参数</h4>
        <div className="rules-card__params">
          <label className="rules-panel__field">
            <span>样本上限</span>
            <input
              type="number"
              min={1}
              max={500}
              value={maxSample}
              onChange={(e) =>
                setDraft({ ...draft, max_sample_size: Number(e.target.value) || 50 })
              }
            />
          </label>
          <label className="rules-panel__field rules-panel__field--wide">
            <span>白名单关键词</span>
            <input
              type="text"
              value={stringifyList(whitelistKeywords)}
              onChange={(e) =>
                setDraft({ ...draft, whitelist_keywords: parseList(e.target.value, false) })
              }
            />
          </label>
          <label className="rules-panel__field rules-panel__field--wide">
            <span>白名单凭证类型</span>
            <input
              type="text"
              value={stringifyList(whitelistTypes)}
              onChange={(e) =>
                setDraft({ ...draft, whitelist_voucher_types: parseList(e.target.value, false) })
              }
            />
          </label>
        </div>
        {isPlainObject(draft.routine_exclusion) && (
          <p className="muted rules-panel__skip">常规分录排除（routine_exclusion）：请用 Agent 调整</p>
        )}
      </div>

      <div className="rules-list">
        {ruleIds.map((ruleId) => {
          const block = draft[ruleId];
          if (!isPlainObject(block)) return null;
          return (
            <RuleCard
              key={ruleId}
              ruleId={ruleId}
              block={block}
              onChange={(next) => setDraft({ ...draft, [ruleId]: next })}
            />
          );
        })}
      </div>
    </div>
  );
}
