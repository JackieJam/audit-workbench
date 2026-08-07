import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, type DetectResponse, type IngestResponse } from "@/api/client";
import { ChartLoadingBar, useSimulatedProgress } from "@/components/ChartLoadingBar";
import { EmptyState } from "@/components/EmptyState";

type Props = {
  projectId: string | null;
  onImported: () => void;
};

function ingestStageLabel(percent: number): string {
  if (percent < 25) return "上传文件中";
  if (percent < 50) return "解析 Excel 中";
  if (percent < 75) return "列映射与清洗中";
  return "写入项目数据中";
}

export function UploadPanel({ projectId, onImported }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [detection, setDetection] = useState<DetectResponse | null>(null);
  const [result, setResult] = useState<IngestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const detect = useMutation({
    mutationFn: () => {
      if (!projectId || files.length === 0) {
        throw new Error("请选择项目并上传文件");
      }
      return api.detectColumns(projectId, files);
    },
    onSuccess: (data) => {
      setDetection(data);
      setResult(null);
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const commit = useMutation({
    mutationFn: () => {
      if (!projectId || files.length === 0) {
        throw new Error("请选择项目并上传文件");
      }
      const mapping = detection?.suggested_mapping ?? {};
      return api.commitIngest(projectId, files, mapping);
    },
    onSuccess: (data) => {
      setResult(data);
      setError(null);
      onImported();
    },
    onError: (e: Error) => setError(e.message),
  });

  const busy = detect.isPending || commit.isPending;
  const progress = useSimulatedProgress(busy);
  const progressLabel = commit.isPending
    ? ingestStageLabel(progress)
    : detect.isPending
      ? "探测列名中"
      : "处理中";

  if (!projectId) {
    return (
      <EmptyState
        kind="project"
        size="sm"
        title="未选择项目"
        description="在上方选择或创建一个项目后，即可上传序时账。"
      />
    );
  }

  return (
    <section className="upload-panel">
      <h3>上传序时账</h3>
      <input
        type="file"
        accept=".xlsx"
        multiple
        onChange={(e) => {
          setFiles(Array.from(e.target.files ?? []));
          setDetection(null);
          setResult(null);
          setError(null);
        }}
      />
      {files.length > 0 && (
        <p className="muted">{files.map((f) => f.name).join("、")}</p>
      )}
      <div className="upload-actions">
        <button
          type="button"
          disabled={!files.length || busy}
          onClick={() => detect.mutate()}
        >
          {detect.isPending ? "探测中…" : "1. 探测列名"}
        </button>
        <button
          type="button"
          className="primary"
          disabled={!files.length || busy}
          onClick={() => commit.mutate()}
        >
          {commit.isPending ? "导入中…" : "2. 确认导入"}
        </button>
      </div>
      <ChartLoadingBar
        loading={busy}
        label={progressLabel}
        hint={
          commit.isPending
            ? "多文件/大表解析可能需要一两分钟"
            : "正在读取表头与样例行"
        }
        percent={progress}
      />
      {error && <p className="error">{error}</p>}
      {detection && !busy && (
        <div className="detect-box">
          <p className="muted">建议映射（并集预览：{detection.file_label}）</p>
          <ul className="mapping-list">
            {Object.entries(detection.suggested_mapping).map(([std, src]) => (
              <li key={std}>
                <code>{std}</code> ← <code>{src}</code>
                {detection.mapping_matches[std] && (
                  <span className="muted">
                    {" "}
                    ({detection.mapping_matches[std].method}{" "}
                    {(detection.mapping_matches[std].score * 100).toFixed(0)}%)
                  </span>
                )}
              </li>
            ))}
          </ul>
          {(detection.per_file?.length ?? 0) > 1 && (
            <div className="per-file-mapping">
              <p className="muted">
                各文件将独立解析映射（避免异构列名静默漏数）。导入时若并集偏好列在某文件不存在，自动回退该文件匹配。
              </p>
              {detection.per_file!.map((fileDet) => (
                <details key={fileDet.file_label} className="per-file-mapping-item">
                  <summary>
                    <code>{fileDet.file_label}</code>
                    <span className="muted"> · {Object.keys(fileDet.suggested_mapping).length} 列</span>
                  </summary>
                  <ul className="mapping-list">
                    {Object.entries(fileDet.suggested_mapping).map(([std, src]) => (
                      <li key={`${fileDet.file_label}-${std}`}>
                        <code>{std}</code> ← <code>{src}</code>
                        {fileDet.mapping_matches[std] && (
                          <span className="muted">
                            {" "}
                            ({fileDet.mapping_matches[std].method}{" "}
                            {(fileDet.mapping_matches[std].score * 100).toFixed(0)}%)
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              ))}
            </div>
          )}
        </div>
      )}
      {result && !busy && (
        <div className="ingest-result">
          <p>已导入 {result.total_rows.toLocaleString()} 行</p>
          <ul>
            {result.year_summary.map((y) => (
              <li key={y.年份}>
                {y.年份} 年：{y.行数} 行 · {y.凭证数} 凭证 · {y.日期范围}
              </li>
            ))}
          </ul>
          {result.missing_columns.length > 0 && (
            <p className="muted">缺失字段（部分分析将降级）：{result.missing_columns.join("、")}</p>
          )}
        </div>
      )}
    </section>
  );
}
