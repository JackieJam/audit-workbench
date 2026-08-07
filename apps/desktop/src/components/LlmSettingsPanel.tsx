import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type LlmPreset, type LlmProfile } from "@/api/client";
import { useLlm } from "@/context/LlmContext";

const EMPTY_FORM = {
  profile_id: "",
  profile_name: "",
  base_url: "https://api.deepseek.com",
  model: "deepseek-chat",
  set_default: true,
};

function hostFromUrl(value: string): string {
  const text = value.trim();
  if (!text) return "";
  try {
    const withScheme = text.includes("://") ? text : `https://${text}`;
    return (new URL(withScheme).hostname || "").toLowerCase();
  } catch {
    return text.toLowerCase();
  }
}

export function LlmSettingsPanel() {
  const queryClient = useQueryClient();
  const { profiles, selectedProfileId, setSelectedProfileId, refreshProfiles, secretBackend } = useLlm();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelsWarning, setModelsWarning] = useState("");
  const [statusMsg, setStatusMsg] = useState("");
  const [allowHostsText, setAllowHostsText] = useState("");
  const [allowEnabled, setAllowEnabled] = useState(false);
  const [allowAll, setAllowAll] = useState(false);

  const presetsQ = useQuery({ queryKey: ["llm-presets"], queryFn: api.getLlmPresets });
  const allowlistQ = useQuery({
    queryKey: ["llm-endpoint-allowlist"],
    queryFn: api.getLlmEndpointAllowlist,
  });

  useEffect(() => {
    const data = allowlistQ.data;
    if (!data) return;
    setAllowEnabled(!!data.enabled);
    setAllowAll(!!data.allow_all);
    setAllowHostsText((data.hosts ?? []).join("\n"));
  }, [allowlistQ.data]);

  const activeProfile = useMemo(
    () => profiles.find((p) => p.profile_id === editingId) ?? null,
    [profiles, editingId],
  );

  useEffect(() => {
    if (editingId && activeProfile) {
      setForm({
        profile_id: activeProfile.profile_id,
        profile_name: activeProfile.profile_name,
        base_url: activeProfile.base_url,
        model: activeProfile.model,
        set_default: !!activeProfile.is_default,
      });
      setApiKey("");
      setModels([]);
      setModelsWarning("");
    }
  }, [editingId, activeProfile]);

  const startNew = () => {
    setEditingId("__new__");
    setForm({ ...EMPTY_FORM, profile_name: `方案 ${profiles.length + 1}` });
    setApiKey("");
    setModels([]);
    setStatusMsg("");
  };

  const saveProfile = useMutation({
    mutationFn: () =>
      api.saveLlmProfile({
        profile_id: form.profile_id,
        profile_name: form.profile_name.trim(),
        base_url: form.base_url.trim(),
        model: form.model.trim(),
        set_default: form.set_default,
      }),
    onSuccess: async (res) => {
      const saved = res.profile;
      setForm({
        profile_id: saved.profile_id,
        profile_name: saved.profile_name,
        base_url: saved.base_url,
        model: saved.model,
        set_default: !!saved.is_default,
      });
      setEditingId(saved.profile_id);
      setSelectedProfileId(saved.profile_id);
      if (apiKey.trim()) {
        await api.saveLlmKey(saved.profile_id, apiKey.trim());
        setApiKey("");
      }
      refreshProfiles();
      setStatusMsg("方案已保存");
    },
  });

  const removeProfile = useMutation({
    mutationFn: (id: string) => api.deleteLlmProfile(id),
    onSuccess: () => {
      refreshProfiles();
      setEditingId(null);
      setStatusMsg("已删除方案");
    },
  });

  const forgetKey = useMutation({
    mutationFn: (id: string) => api.forgetLlmKey(id),
    onSuccess: () => {
      refreshProfiles();
      setStatusMsg("已清除本机记住的密钥");
    },
  });

  const fetchModels = useMutation({
    mutationFn: () =>
      api.fetchLlmModels({
        profile_id: form.profile_id || undefined,
        base_url: form.base_url,
        api_key: apiKey || undefined,
      }),
    onSuccess: (res) => {
      setModels(res.models);
      setModelsWarning(res.warning ?? "");
      if (res.models.length && !res.models.includes(form.model)) {
        setForm((f) => ({ ...f, model: res.models[0] }));
      }
    },
  });

  const testPing = useMutation({
    mutationFn: () =>
      api.pingLlm({
        profile_id: form.profile_id || undefined,
        base_url: form.base_url,
        model: form.model,
        api_key: apiKey || undefined,
      }),
    onSuccess: (res) => {
      setStatusMsg(`连接成功 · ${res.model} · 密钥来源：${res.key_source}`);
    },
    onError: (err) => setStatusMsg(String(err)),
  });

  const saveAllowlist = useMutation({
    mutationFn: () => {
      const hosts = allowHostsText
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean);
      return api.saveLlmEndpointAllowlist({
        enabled: allowEnabled,
        allow_all: allowAll,
        hosts,
      });
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["llm-endpoint-allowlist"], data);
      setStatusMsg(
        data.enabled
          ? data.allow_all
            ? "白名单已保存：允许全部 endpoint"
            : `白名单已保存：${data.hosts.length} 个 host`
          : "白名单已关闭（不强制拦截）",
      );
    },
  });

  const applyPreset = (preset: LlmPreset) => {
    if (!preset.base_url) return;
    setForm((f) => ({
      ...f,
      base_url: preset.base_url,
      model: preset.default_model || f.model,
    }));
  };

  const seedAllowlistHosts = () => {
    const seeds = allowlistQ.data?.seed_hosts ?? [];
    const current = new Set(
      allowHostsText
        .split(/[\n,]/)
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean),
    );
    for (const host of seeds) current.add(host);
    setAllowHostsText([...current].join("\n"));
    setAllowEnabled(true);
    setAllowAll(false);
  };

  const addCurrentBaseUrlHost = () => {
    const host = hostFromUrl(form.base_url);
    if (!host) return;
    const lines = allowHostsText
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!lines.map((h) => h.toLowerCase()).includes(host)) {
      setAllowHostsText([...lines, host].join("\n"));
    }
    setAllowEnabled(true);
    setAllowAll(false);
  };

  const presets = presetsQ.data?.presets ?? [];
  const allowlist = allowlistQ.data;
  const envLocked = !!allowlist?.env_overrides_file;
  const currentHost = hostFromUrl(form.base_url);
  const hostAllowed =
    !allowEnabled ||
    allowAll ||
    (currentHost
      ? allowHostsText
          .split(/[\n,]/)
          .map((item) => item.trim().toLowerCase())
          .filter(Boolean)
          .includes(currentHost)
      : false);

  return (
    <div className="llm-settings">
      <header className="llm-settings__head">
        <div>
          <h2>大模型配置</h2>
          <p className="muted">
            支持多套方案切换；密钥仅存本机（{secretBackend || presetsQ.data?.secret_backend || "file"}），不会写入方案文件。
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={startNew}>
          新建方案
        </button>
      </header>

      <section className="llm-allowlist">
        <div className="llm-allowlist__head">
          <div>
            <h3>Endpoint 白名单</h3>
            <p className="muted">
              启用后，保存方案 / 拉取模型 / 测试连接 / 凭证核实只会访问列表中的 host。
              {envLocked
                ? " 当前由环境变量 AUDIT_WORKBENCH_LLM_ENDPOINT_ALLOWLIST 接管，UI 只读。"
                : " 未启用时不强制拦截（兼容本地开发）。"}
            </p>
          </div>
          <span className="llm-badge">
            来源：{allowlist?.source ?? "…"}
            {allowlist?.enabled ? (allowlist.allow_all ? " · 全放行" : " · 已启用") : " · 未启用"}
          </span>
        </div>

        <div className="llm-allowlist__controls">
          <label className="llm-checkbox">
            <input
              type="checkbox"
              checked={allowEnabled}
              disabled={envLocked}
              onChange={(e) => setAllowEnabled(e.target.checked)}
            />
            启用白名单强制校验
          </label>
          <label className="llm-checkbox">
            <input
              type="checkbox"
              checked={allowAll}
              disabled={envLocked || !allowEnabled}
              onChange={(e) => setAllowAll(e.target.checked)}
            />
            允许全部 endpoint（等同 *）
          </label>
        </div>

        <label>
          允许的 Host（每行一个，或逗号分隔）
          <textarea
            className="llm-allowlist__textarea"
            value={allowHostsText}
            disabled={envLocked || allowAll}
            onChange={(e) => setAllowHostsText(e.target.value)}
            rows={5}
            placeholder={"api.deepseek.com\napi.openai.com\nlocalhost"}
          />
        </label>

        <div className="llm-form__actions">
          <button
            type="button"
            className="btn-primary"
            disabled={envLocked || saveAllowlist.isPending}
            onClick={() => saveAllowlist.mutate()}
          >
            {saveAllowlist.isPending ? "保存中…" : "保存白名单"}
          </button>
          <button type="button" className="btn-ghost" disabled={envLocked} onClick={seedAllowlistHosts}>
            填入预设 Host
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={envLocked || !currentHost}
            onClick={addCurrentBaseUrlHost}
          >
            加入当前 Base URL
          </button>
        </div>
        {saveAllowlist.isError && <p className="error">{String(saveAllowlist.error)}</p>}
        {allowEnabled && !allowAll && editingId && !hostAllowed && (
          <p className="error">当前 Base URL 的 host「{currentHost || "空"}」不在白名单中，保存/连接将被拒绝。</p>
        )}
      </section>

      <div className="llm-settings__body">
        <aside className="llm-profile-list">
          <h3>已保存方案</h3>
          {profiles.length === 0 && <p className="muted">暂无方案，点击「新建方案」开始配置。</p>}
          {profiles.map((p: LlmProfile) => (
            <button
              key={p.profile_id}
              type="button"
              className={
                editingId === p.profile_id || (editingId === null && selectedProfileId === p.profile_id)
                  ? "llm-profile-card active"
                  : "llm-profile-card"
              }
              onClick={() => {
                setEditingId(p.profile_id);
                setSelectedProfileId(p.profile_id);
              }}
            >
              <div className="llm-profile-card__title">
                {p.profile_name}
                {p.is_default && <span className="llm-badge">默认</span>}
              </div>
              <div className="muted">{p.model}</div>
              <div className="muted llm-profile-card__url">{p.base_url}</div>
              <div className="llm-profile-card__meta">
                {p.key_configured ? "🔒 已配置密钥" : "⚠ 未配置密钥"}
              </div>
            </button>
          ))}
        </aside>

        <section className="llm-editor">
          {!editingId ? (
            <div className="llm-editor__empty">
              <p className="muted">选择左侧方案进行编辑，或新建一套大模型配置。</p>
              <p className="muted">当前全局选用：{profiles.find((p) => p.profile_id === selectedProfileId)?.profile_name ?? "未选择"}</p>
            </div>
          ) : (
            <form
              className="llm-form"
              onSubmit={(e) => {
                e.preventDefault();
                saveProfile.mutate();
              }}
            >
              <div className="llm-form__row">
                <label>
                  方案名称
                  <input
                    value={form.profile_name}
                    onChange={(e) => setForm((f) => ({ ...f, profile_name: e.target.value }))}
                    required
                  />
                </label>
                <label className="llm-checkbox">
                  <input
                    type="checkbox"
                    checked={form.set_default}
                    onChange={(e) => setForm((f) => ({ ...f, set_default: e.target.checked }))}
                  />
                  设为默认方案
                </label>
              </div>

              <div className="llm-presets">
                <span className="muted">快速选择提供商：</span>
                {presets.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className="btn-ghost"
                    onClick={() => applyPreset(p)}
                    disabled={!p.base_url}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              <label>
                Base URL
                <input
                  value={form.base_url}
                  onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
                  placeholder="https://api.deepseek.com"
                  required
                />
              </label>

              <div className="llm-form__row llm-model-row">
                <label className="llm-model-input">
                  模型
                  <input
                    value={form.model}
                    onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                    list="llm-model-options"
                    required
                  />
                  <datalist id="llm-model-options">
                    {models.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                </label>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => fetchModels.mutate()}
                  disabled={fetchModels.isPending}
                >
                  {fetchModels.isPending ? "获取中…" : "获取模型列表"}
                </button>
              </div>
              {modelsWarning && <p className="muted">{modelsWarning}</p>}
              {models.length > 0 && (
                <p className="muted">已加载 {models.length} 个模型（来源：{fetchModels.data?.source ?? "api"}）</p>
              )}

              <label>
                API Key
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={
                    activeProfile?.key_configured
                      ? "留空则保留已记住的密钥；填写则更新"
                      : "保存时一并写入本机密钥存储"
                  }
                />
              </label>

              <div className="llm-form__actions">
                <button type="submit" className="btn-primary" disabled={saveProfile.isPending}>
                  {saveProfile.isPending ? "保存中…" : "保存方案"}
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => testPing.mutate()}
                  disabled={testPing.isPending}
                >
                  {testPing.isPending ? "测试中…" : "测试连接"}
                </button>
                {form.profile_id && activeProfile?.key_configured && (
                  <button type="button" className="btn-ghost" onClick={() => forgetKey.mutate(form.profile_id)}>
                    清除密钥
                  </button>
                )}
                {form.profile_id && (
                  <button
                    type="button"
                    className="btn-danger"
                    onClick={() => {
                      if (confirm("确定删除该方案？本机密钥需单独清除。")) removeProfile.mutate(form.profile_id);
                    }}
                  >
                    删除方案
                  </button>
                )}
              </div>

              {(saveProfile.isError || fetchModels.isError || testPing.isError) && (
                <p className="error">
                  {String(saveProfile.error || fetchModels.error || testPing.error)}
                </p>
              )}
              {statusMsg && <p className="muted">{statusMsg}</p>}
            </form>
          )}
        </section>
      </div>
    </div>
  );
}
