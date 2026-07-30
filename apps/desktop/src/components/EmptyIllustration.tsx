export type EmptyIllustrationKind =
  | "project"
  | "upload"
  | "search"
  | "chart"
  | "clipboard"
  | "shield"
  | "ai"
  | "alert";

/**
 * 空状态插画：描边几何风，颜色取自设计令牌（currentColor + CSS var），
 * 随浅色/深色主题自动适配。
 */
export function EmptyIllustration({ kind }: { kind: EmptyIllustrationKind }) {
  const common = {
    className: "empty-ill",
    viewBox: "0 0 160 120",
    fill: "none",
    "aria-hidden": true as const,
  };

  switch (kind) {
    case "upload":
      return (
        <svg {...common}>
          <path
            d="M34 78h20l6 10h40l6-10h20v18a8 8 0 0 1-8 8H42a8 8 0 0 1-8-8Z"
            fill="var(--panel)"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinejoin="round"
          />
          <line x1="80" y1="64" x2="80" y2="30" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />
          <path
            d="M66 43 80 29l14 14"
            stroke="var(--accent)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <rect x="62" y="14" width="36" height="6" rx="3" fill="var(--accent-soft)" />
        </svg>
      );
    case "search":
      return (
        <svg {...common}>
          <rect x="26" y="22" width="76" height="74" rx="9" fill="var(--panel)" stroke="currentColor" strokeWidth="2.5" />
          <line x1="39" y1="39" x2="86" y2="39" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" opacity=".55" />
          <line x1="39" y1="53" x2="76" y2="53" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" opacity=".55" />
          <line x1="39" y1="67" x2="82" y2="67" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" opacity=".55" />
          <circle cx="108" cy="76" r="19" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="3" />
          <line x1="122" y1="90" x2="135" y2="103" stroke="var(--accent)" strokeWidth="3.5" strokeLinecap="round" />
        </svg>
      );
    case "chart":
      return (
        <svg {...common}>
          <path d="M34 20v72a6 6 0 0 0 6 6h88" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
          <rect x="49" y="64" width="14" height="28" rx="3.5" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="2.5" />
          <rect x="71" y="50" width="14" height="42" rx="3.5" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="2.5" />
          <rect x="93" y="72" width="14" height="20" rx="3.5" fill="var(--panel)" stroke="currentColor" strokeWidth="2.5" />
          <path
            d="M50 44c14-5 24-13 42-9s22-7 30-11"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeDasharray="1 7"
            opacity=".7"
          />
        </svg>
      );
    case "clipboard":
      return (
        <svg {...common}>
          <rect x="44" y="24" width="72" height="80" rx="9" fill="var(--panel)" stroke="currentColor" strokeWidth="2.5" />
          <rect x="66" y="14" width="28" height="16" rx="5" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="2.5" />
          <line x1="58" y1="48" x2="102" y2="48" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" opacity=".55" />
          <line x1="58" y1="62" x2="92" y2="62" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" opacity=".55" />
          <path
            d="m58 82 7 7 14-15"
            stroke="var(--accent)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "shield":
      return (
        <svg {...common}>
          <path
            d="M80 16l40 14v26c0 26-17 41-40 49-23-8-40-23-40-49V30Z"
            fill="var(--accent-soft)"
            stroke="var(--accent)"
            strokeWidth="3"
            strokeLinejoin="round"
          />
          <path
            d="m63 59 12 12 23-25"
            stroke="var(--accent)"
            strokeWidth="4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "ai":
      return (
        <svg {...common}>
          <rect x="36" y="30" width="88" height="56" rx="14" fill="var(--panel)" stroke="currentColor" strokeWidth="2.5" />
          <circle cx="66" cy="58" r="5" fill="var(--accent)" />
          <circle cx="94" cy="58" r="5" fill="var(--accent)" />
          <path d="M71 72q9 7 18 0" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
          <line x1="80" y1="30" x2="80" y2="21" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
          <circle cx="80" cy="15" r="4.5" fill="var(--accent-soft)" stroke="var(--accent)" strokeWidth="2.5" />
          <path d="m126 82 2.6 5.7 5.7 2.6-5.7 2.6-2.6 5.7-2.6-5.7-5.7-2.6 5.7-2.6Z" fill="var(--accent)" />
        </svg>
      );
    case "alert":
      return (
        <svg {...common}>
          <path
            d="M80 20l54 76a6 6 0 0 1-5.2 9H31.2a6 6 0 0 1-5.2-9Z"
            fill="var(--warning-soft)"
            stroke="var(--warning)"
            strokeWidth="3"
            strokeLinejoin="round"
          />
          <line x1="80" y1="50" x2="80" y2="71" stroke="var(--warning)" strokeWidth="4" strokeLinecap="round" />
          <circle cx="80" cy="85" r="3.2" fill="var(--warning)" />
        </svg>
      );
    case "project":
    default:
      return (
        <svg {...common}>
          <rect x="56" y="18" width="50" height="40" rx="7" fill="var(--panel)" stroke="currentColor" strokeWidth="2.5" />
          <line x1="65" y1="30" x2="97" y2="30" stroke="var(--accent)" strokeWidth="2.5" strokeLinecap="round" />
          <line x1="65" y1="40" x2="89" y2="40" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" opacity=".55" />
          <path
            d="M28 100V76a8 8 0 0 1 8-8h24l10 10h52a8 8 0 0 1 8 8v14a8 8 0 0 1-8 8H36a8 8 0 0 1-8-8Z"
            fill="var(--accent-soft)"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinejoin="round"
          />
        </svg>
      );
  }
}
