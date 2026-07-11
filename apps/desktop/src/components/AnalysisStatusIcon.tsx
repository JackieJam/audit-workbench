type Status = "running" | "success" | "error" | "idle";

type Props = {
  status: Status;
  size?: number;
};

export function AnalysisStatusIcon({ status, size = 18 }: Props) {
  if (status === "idle") return null;

  const common = { width: size, height: size, viewBox: "0 0 24 24", "aria-hidden": true as const };

  if (status === "running") {
    return (
      <svg {...common} className="analysis-status-icon analysis-status-icon--running">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.25" />
        <path
          d="M12 3a9 9 0 0 1 9 9"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    );
  }

  if (status === "success") {
    return (
      <svg {...common} className="analysis-status-icon analysis-status-icon--success">
        <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M8 12.5 10.8 15.2 16 9.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  return (
    <svg {...common} className="analysis-status-icon analysis-status-icon--error">
      <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M9 9l6 6M15 9l-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
