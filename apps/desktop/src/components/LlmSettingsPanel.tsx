import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type LlmPreset, type LlmProfile } from "@/api/client";
import { useLlm } from "@/context/LlmContext";

const EMPTY_FORM = {
  profile_id: "",
  profile_name: "",
  base_url: "https://api.deepseek.com",
  model: "deepseek-chat",
  set_default: true,
};

export function LlmSettingsPanel() {
  const { profiles, selectedProfileId, setSelectedProfileId, refreshProfiles, secretBackend } = useLlm();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelsWarning, setModelsWarning] = useState("");
  const [statusMsg, setStatusMsg] = useState("");

  const presetsQ = useQuery({ queryKey: ["llm-presets"], queryFn: api.getLlmPresets });

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

  const applyPreset = (preset: LlmPreset) => {
    if (!preset.base_url) return;
    setForm((f) => ({
      ...f,
      base_url: preset.base_url,
      model: preset.default_model || f.model,
    }));
  };

  const presets = presetsQ.data?.presets ?? [];

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
