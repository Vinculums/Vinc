import type { ReactNode, ButtonHTMLAttributes } from "react";
import { Icon } from "./Icon";
import type { IconName } from "../lib/icons";

// 기본 컴포넌트 세트(DESIGN.md §5). 모노크롬 · 화면당 primary 1개 · 카드 절제.

// ── Button ──────────────────────────────────────────────
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost";
  size?: "md" | "sm";
  icon?: IconName;
  children?: ReactNode;
};
export function Button({
  variant = "ghost",
  size = "md",
  icon,
  children,
  className = "",
  ...rest
}: ButtonProps) {
  const cls = [
    "btn",
    `btn--${variant}`,
    size === "sm" ? "btn--sm" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={cls} {...rest}>
      {icon && <Icon name={icon} size={size === "sm" ? 16 : 18} />}
      {children}
    </button>
  );
}

// ── Tab ─────────────────────────────────────────────────
export function Tabs<T extends string>({
  items,
  value,
  onChange,
}: {
  items: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((it) => (
        <button
          key={it.value}
          role="tab"
          aria-selected={value === it.value}
          className="tab"
          onClick={() => onChange(it.value)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

// ── ListRow ─────────────────────────────────────────────
export function ListRow({
  title,
  meta,
  leading,
  trailing,
  selected,
  onClick,
}: {
  title: ReactNode;
  meta?: ReactNode;
  leading?: ReactNode;
  trailing?: ReactNode;
  selected?: boolean;
  onClick?: () => void;
}) {
  return (
    <div
      className={`listrow ${onClick ? "listrow--interactive" : ""}`}
      aria-selected={selected}
      onClick={onClick}
    >
      {leading}
      <div className="listrow__body">
        <div className="listrow__title">{title}</div>
        {meta && <div className="listrow__meta">{meta}</div>}
      </div>
      {trailing}
    </div>
  );
}

// ── Progress ────────────────────────────────────────────
export function Progress({ value }: { value?: number }) {
  const indeterminate = value == null;
  return (
    <div className={`progress ${indeterminate ? "progress--indeterminate" : ""}`}>
      <div
        className="progress__bar"
        style={indeterminate ? undefined : { width: `${Math.round(value * 100)}%` }}
      />
    </div>
  );
}

// ── Skeleton ────────────────────────────────────────────
export function Skeleton({
  height = 16,
  width = "100%",
}: {
  height?: number | string;
  width?: number | string;
}) {
  return <div className="skeleton" style={{ height, width }} />;
}

// ── EmptyGuide ──────────────────────────────────────────
export function EmptyGuide({
  icon = "ring",
  title,
  hint,
  action,
}: {
  icon?: IconName;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty__icon">
        <Icon name={icon} size={32} />
      </span>
      <div className="empty__title">{title}</div>
      {hint && <div>{hint}</div>}
      {action}
    </div>
  );
}

// ── ErrorBanner (error = 굵은 잉크 + alert) ─────────────
export function ErrorBanner({
  message,
  detail,
  onRetry,
}: {
  message: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="errorbanner" role="alert">
      <Icon name="alert" size={20} />
      <div className="errorbanner__body">
        <div>{message}</div>
        {detail && <div className="errorbanner__detail">{detail}</div>}
      </div>
      {onRetry && (
        <Button size="sm" variant="ghost" icon="sync" onClick={onRetry}>
          다시 시도
        </Button>
      )}
    </div>
  );
}

// ── StatusMark (색 아닌 형태 + 텍스트 무게) ──────────────
export type FourState = "idle" | "loading" | "success" | "error";
const STATUS_ICON: Record<FourState, IconName> = {
  idle: "ring",
  loading: "sync",
  success: "disc",
  error: "alert",
};
export function StatusMark({ state, label }: { state: FourState; label?: string }) {
  return (
    <span className={`statusmark statusmark--${state}`}>
      <span className={state === "loading" ? "spin" : undefined}>
        <Icon name={STATUS_ICON[state]} size={16} />
      </span>
      {label && <span>{label}</span>}
    </span>
  );
}
