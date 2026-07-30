import type { ReactNode } from "react";
import { EmptyIllustration, type EmptyIllustrationKind } from "./EmptyIllustration";

type Props = {
  kind?: EmptyIllustrationKind;
  title: string;
  description?: string;
  action?: ReactNode;
  size?: "md" | "sm";
};

export function EmptyState({ kind = "project", title, description, action, size = "md" }: Props) {
  return (
    <div className={`empty-state${size === "sm" ? " empty-state--sm" : ""}`} role="status">
      <EmptyIllustration kind={kind} />
      <p className="empty-state__title">{title}</p>
      {description ? <p className="empty-state__desc">{description}</p> : null}
      {action ? <div className="empty-state__action">{action}</div> : null}
    </div>
  );
}
