import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_KEY = "audit-workbench.agent-panel-width";
const DEFAULT_WIDTH = 380;
const MIN_WIDTH = 280;
const MAX_WIDTH = 720;

function readStoredWidth(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const n = raw == null ? DEFAULT_WIDTH : Number(raw);
    if (!Number.isFinite(n)) return DEFAULT_WIDTH;
    return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(n)));
  } catch {
    return DEFAULT_WIDTH;
  }
}

function clampWidth(width: number, workspaceWidth: number): number {
  // Keep room for the main pane; never force document-level overflow.
  const maxByWorkspace = Math.max(200, workspaceWidth - 360);
  const floor = Math.min(MIN_WIDTH, maxByWorkspace);
  return Math.min(MAX_WIDTH, maxByWorkspace, Math.max(floor, Math.round(width)));
}

/** 右侧审计助手面板宽度（可拖拽，持久化；窗口变化时自动回夹）。 */
export function useAgentPanelWidth() {
  const [width, setWidth] = useState(readStoredWidth);
  const [collapsed, setCollapsed] = useState(false);
  const dragging = useRef(false);
  const workspaceRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(width));
    } catch {
      /* ignore quota */
    }
  }, [width]);

  const setClampedWidth = useCallback((next: number) => {
    const workspace = workspaceRef.current;
    const workspaceWidth = workspace?.getBoundingClientRect().width ?? 1200;
    setWidth(clampWidth(next, workspaceWidth));
  }, []);

  // Reclamp when the workspace size changes (window resize / browser zoom).
  useEffect(() => {
    const el = workspaceRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const workspaceWidth = entry.contentRect.width;
      setWidth((current) => clampWidth(current, workspaceWidth));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const onSplitterPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    dragging.current = true;
    document.body.classList.add("is-resizing-agent");

    const onMove = (ev: PointerEvent) => {
      if (!dragging.current) return;
      const workspace = workspaceRef.current;
      if (!workspace) return;
      const rect = workspace.getBoundingClientRect();
      setWidth(clampWidth(rect.right - ev.clientX, rect.width));
    };

    const onUp = (ev: PointerEvent) => {
      dragging.current = false;
      document.body.classList.remove("is-resizing-agent");
      try {
        handle.releasePointerCapture(ev.pointerId);
      } catch {
        /* already released */
      }
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  }, []);

  const resetWidth = useCallback(() => setClampedWidth(DEFAULT_WIDTH), [setClampedWidth]);
  const collapse = useCallback(() => setCollapsed(true), []);
  const expand = useCallback(() => setCollapsed(false), []);

  const nudgeWidth = useCallback(
    (delta: number) => {
      setClampedWidth(width + delta);
    },
    [setClampedWidth, width],
  );

  return {
    width,
    collapsed,
    workspaceRef,
    onSplitterPointerDown,
    resetWidth,
    nudgeWidth,
    collapse,
    expand,
  };
}
