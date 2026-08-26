import { useState, type ReactNode } from "react";

const NOTICE_ICONS = {
  info: "💡",
  ok: "✨",
  warn: "⚡",
  error: "💥",
} as const;

export function Notice({
  kind = "info",
  children,
}: {
  kind?: "info" | "error" | "ok" | "warn";
  children: ReactNode;
}) {
  return (
    <div className={`notice ${kind}`} role={kind === "error" ? "alert" : undefined}>
      <span className="notice-icon" aria-hidden="true">
        {NOTICE_ICONS[kind]}
      </span>
      <div className="notice-body">{children}</div>
    </div>
  );
}

export function Empty({
  title,
  hint,
  illustration,
}: {
  title: string;
  hint?: string;
  illustration?: ReactNode;
}) {
  return (
    <div className="empty">
      {illustration && <div className="empty-illustration">{illustration}</div>}
      <p className="empty-title">{title}</p>
      {hint && <p className="empty-hint">{hint}</p>}
    </div>
  );
}

/**
 * Animated 3-dot typing indicator for thinking/streaming states.
 */
export function TypingIndicator({ label }: { label?: string }) {
  return (
    <div className="typing-indicator" aria-label={label ?? "Thinking..."}>
      <span className="dot-pulse" />
      <span className="dot-pulse" />
      <span className="dot-pulse" />
      {label && <span className="typing-label">{label}</span>}
    </div>
  );
}

/**
 * A spinner that takes the colour of whatever it is inside.
 */
export function Spinner({ label }: { label?: string }) {
  return (
    <span style={{ display: "inline-flex", gap: 9, alignItems: "center", color: "inherit" }}>
      <span className="spin" aria-hidden="true" />
      {label && <span style={{ fontSize: "0.9rem" }}>{label}</span>}
    </span>
  );
}

export function ProjectPicker({
  value,
  onChange,
  projects,
  allowNew = true,
  allLabel,
}: {
  value: string;
  onChange: (next: string) => void;
  projects: string[];
  allowNew?: boolean;
  allLabel?: string;
}) {
  const NEW = "__new__";
  const [isCreatingNew, setIsCreatingNew] = useState(false);

  const isCustom = allowNew && value !== "" && !projects.includes(value);
  const showFreeText = allowNew && (isCreatingNew || isCustom);

  return (
    <div className="stack" style={{ gap: 8 }}>
      <select
        value={showFreeText ? NEW : (projects.includes(value) ? value : "")}
        onChange={(event) => {
          const next = event.target.value;
          if (next === NEW) {
            setIsCreatingNew(true);
            onChange("");
          } else {
            setIsCreatingNew(false);
            onChange(next);
          }
        }}
        aria-label="Project"
      >
        <option value="">{allLabel ?? "Let the Librarian decide"}</option>
        {projects.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
        {allowNew && <option value={NEW}>New project...</option>}
      </select>

      {showFreeText && (
        <input
          type="text"
          value={value}
          autoFocus
          placeholder="New project name"
          onChange={(event) => onChange(event.target.value)}
          aria-label="New project name"
        />
      )}
    </div>
  );
}
