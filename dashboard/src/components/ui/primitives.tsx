"use client";
/**
 * Shadcn-style UI primitives (adapted to Quenbot dark theme).
 * Minimal, Tailwind-only, no external deps.
 */
import * as React from "react";

/* cn() util */
export function cn(...xs: Array<string | false | null | undefined>): string {
  return xs.filter(Boolean).join(" ");
}

/* ───────────── Card ───────────── */
export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-surface-border bg-surface-card/80 shadow-sm backdrop-blur-sm",
        className
      )}
      {...p}
    />
  );
}
export function CardHeader({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1 p-4 pb-2", className)} {...p} />;
}
export function CardTitle({ className, ...p }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3 className={cn("text-sm font-semibold leading-none tracking-tight text-gray-100", className)} {...p} />
  );
}
export function CardDescription({ className, ...p }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-[11px] text-gray-500", className)} {...p} />;
}
export function CardContent({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4 pt-2", className)} {...p} />;
}

/* ───────────── Badge ───────────── */
type BadgeVariant = "default" | "success" | "warn" | "danger" | "info" | "outline" | "muted";
const BADGE_VARIANT: Record<BadgeVariant, string> = {
  default: "bg-accent/15 text-accent border-accent/30",
  success: "bg-bull/15 text-bull border-bull/30",
  warn:    "bg-warn/15 text-warn border-warn/30",
  danger:  "bg-bear/15 text-bear border-bear/30",
  info:    "bg-sky-500/15 text-sky-300 border-sky-500/30",
  outline: "bg-transparent text-gray-300 border-surface-border",
  muted:   "bg-gray-700/30 text-gray-400 border-gray-700/40",
};
export function Badge({
  variant = "default",
  className,
  children,
  ...p
}: React.HTMLAttributes<HTMLSpanElement> & { variant?: BadgeVariant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium",
        BADGE_VARIANT[variant],
        className
      )}
      {...p}
    >
      {children}
    </span>
  );
}

/* ───────────── Progress ───────────── */
export function Progress({
  value,
  max = 100,
  tone = "accent",
  className,
}: {
  value: number;
  max?: number;
  tone?: "accent" | "bull" | "bear" | "warn";
  className?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const bar =
    tone === "bull" ? "bg-bull" : tone === "bear" ? "bg-bear" : tone === "warn" ? "bg-warn" : "bg-accent";
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-border", className)}>
      <div className={cn("h-full transition-all", bar)} style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ───────────── Tabs ───────────── */
export function Tabs({
  value,
  onChange,
  items,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  items: Array<{ value: string; label: React.ReactNode; icon?: React.ReactNode }>;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap gap-1 rounded-lg bg-surface-card/60 p-1", className)}>
      {items.map((it) => {
        const active = it.value === value;
        return (
          <button
            key={it.value}
            onClick={() => onChange(it.value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] font-medium transition-all",
              active
                ? "bg-accent text-white shadow-sm"
                : "text-gray-400 hover:bg-surface-hover hover:text-gray-200"
            )}
          >
            {it.icon}
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

/* ───────────── Stat (KPI) ───────────── */
export function Stat({
  label,
  value,
  hint,
  tone = "default",
  icon,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "default" | "bull" | "bear" | "warn";
  icon?: React.ReactNode;
}) {
  const color =
    tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : tone === "warn" ? "text-warn" : "text-gray-100";
  return (
    <Card className="min-w-0 p-2.5">
      <div className="flex min-w-0 items-center justify-between gap-1">
        <span className="truncate text-[10px] uppercase tracking-wide text-gray-500">{label}</span>
        {icon && <span className="shrink-0 text-gray-500">{icon}</span>}
      </div>
      <div className={cn("mt-1 truncate font-mono text-lg font-semibold tabular-nums", color)}>{value}</div>
      {hint && <div className="mt-0.5 truncate text-[10px] text-gray-500">{hint}</div>}
    </Card>
  );
}

/* ───────────── Empty State ───────────── */
export function EmptyState({
  title,
  description,
  icon,
}: {
  title: string;
  description?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-surface-border bg-surface-card/30 p-6 text-center">
      {icon && <div className="text-gray-600">{icon}</div>}
      <div className="text-sm font-medium text-gray-300">{title}</div>
      {description && <div className="text-[11px] text-gray-500 max-w-xs">{description}</div>}
    </div>
  );
}
