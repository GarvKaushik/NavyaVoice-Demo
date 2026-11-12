/* ─────────────────────────────────────────────────────────────
 * Shared portal pieces: layout, cards, tables, forms, data loading.
 * ─────────────────────────────────────────────────────────── */
import { useCallback, useEffect, useState } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { get } from "./api";
import { Icon } from "./icons";
import type { IconName } from "./icons";
import { Logo } from "./Landing";

export function useData<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      setData(await get<T>(path));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, loading, reload };
}

export function useHashRoute(defaultRoute: string): [string, (route: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, "") || defaultRoute;
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    onChange(); // the hash may have changed before this listener was attached
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultRoute]);
  return [route, (next) => (window.location.hash = `#/${next}`)];
}

export interface NavItem {
  key: string;
  label: string;
  icon: IconName;
  group?: string;
  count?: number;
}

const COLLAPSED_KEY = "portal.sidebar.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/** Left sidebar layout: logo, account, grouped navigation and a summary box at the bottom.
 *  On a phone the sidebar slides in from a menu button. */
export function Shell({ title, subtitle, nav, route, onNavigate, onSignOut, children, badge, footer }: {
  title: string;
  subtitle: string;
  nav: NavItem[];
  route: string;
  onNavigate: (key: string) => void;
  onSignOut: () => void;
  children: ReactNode;
  badge?: ReactNode;
  footer?: ReactNode;
}) {
  const current = route.split("/")[0];
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [open, setOpen] = useState(false);
  const page = nav.find((item) => item.key === current)?.label ?? title;
  const slim = collapsed && !open; // the phone drawer always shows labels

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    try {
      window.localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
    } catch {
      /* the choice just isn't remembered */
    }
  }

  function go(key: string) {
    setOpen(false);
    onNavigate(key);
  }

  return (
    <div className="min-h-screen text-gray-900 lg:flex">
      {open && <div className="fixed inset-0 z-30 bg-black/25 backdrop-blur-sm lg:hidden" onClick={() => setOpen(false)} aria-hidden="true" />}

      {/* Dark navigation panel covering the left side */}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col bg-neutral-900 text-neutral-100 transition-transform
        lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"} ${slim ? "lg:w-[4.5rem]" : "lg:w-64"}`}>
        <div className={`flex items-center gap-2 px-4 pt-5 pb-4 ${slim ? "lg:flex-col lg:px-2" : "justify-between"}`}>
          <Logo compact={slim} />
          <button onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="hidden lg:flex h-9 w-9 items-center justify-center rounded-full text-neutral-400 hover:bg-white/10 hover:text-white">
            <Icon name="panel" />
          </button>
          <button onClick={() => setOpen(false)} aria-label="Close menu"
            className="lg:hidden flex h-9 w-9 items-center justify-center rounded-full text-neutral-400 hover:bg-white/10 hover:text-white">
            <Icon name="close" />
          </button>
        </div>

        <div className={`mx-3 mb-3 flex items-center gap-2 border border-white/10 bg-white/5 p-2 ${slim ? "lg:justify-center lg:p-1.5" : ""}`}>
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-violet-300 text-xs font-semibold text-neutral-900" aria-hidden="true">
            {(title.trim()[0] ?? "?").toUpperCase()}
          </span>
          <div className={`min-w-0 flex-1 ${slim ? "lg:hidden" : ""}`}>
            <p className="truncate text-sm font-medium text-white">{title}</p>
            <p className="truncate text-xs text-neutral-400">{subtitle}</p>
          </div>
          <button onClick={onSignOut} aria-label="Sign out" title="Sign out"
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-neutral-400 hover:bg-white/10 hover:text-white ${slim ? "lg:hidden" : ""}`}>
            <Icon name="logout" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 pb-3" aria-label="Main">
          {nav.map((item, i) => (
            <div key={item.key}>
              {item.group && item.group !== nav[i - 1]?.group && (
                <p className={`mt-5 mb-1.5 px-3 font-mono text-[10px] uppercase tracking-[0.14em] text-neutral-400 ${slim ? "lg:sr-only" : ""}`}>{item.group}</p>
              )}
              <button onClick={() => go(item.key)} title={slim ? item.label : undefined}
                aria-current={current === item.key ? "page" : undefined}
                className={`relative mb-0.5 flex w-full items-center gap-3 rounded-full px-3 py-2.5 text-sm transition-colors ${slim ? "lg:justify-center lg:px-0" : ""} ${
                  current === item.key ? "bg-white font-medium text-neutral-900" : "text-neutral-300 hover:bg-white/10 hover:text-white"
                }`}>
                <Icon name={item.icon} className="h-[18px] w-[18px]" />
                <span className={`flex-1 truncate text-left ${slim ? "lg:sr-only" : ""}`}>{item.label}</span>
                {item.count ? (
                  <span className={`flex h-5 min-w-5 items-center justify-center rounded-full bg-lime-300 px-1.5 text-[11px] font-semibold text-neutral-900 ${slim ? "lg:absolute lg:right-1 lg:top-0.5" : ""}`}>{item.count}</span>
                ) : null}
              </button>
            </div>
          ))}
        </nav>

        {footer && <div className={`p-3 ${slim ? "lg:hidden" : ""}`}>{footer}</div>}
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 bg-[#f2f2ee]/75 backdrop-blur-xl">
          <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 pt-6 pb-3 lg:px-8">
            <button onClick={() => setOpen(true)} aria-label="Open menu"
              className="lg:hidden -ml-1 flex h-10 w-10 items-center justify-center rounded-full glass text-gray-800">
              <Icon name="menu" className="h-5 w-5" />
            </button>
            <h1 className="min-w-0 flex-1 truncate text-2xl font-semibold tracking-tight text-gray-900 sm:text-3xl">{page}</h1>
            {badge}
          </div>
        </header>
        <main key={current} className="page-in mx-auto max-w-7xl px-4 pb-10 pt-3 space-y-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}

/** The box pinned to the bottom of the sidebar: a lime highlight block. */
export function SidebarBox({ label, value, action }: { label: string; value: ReactNode; action?: ReactNode }) {
  return (
    <div className="bg-lime-300 p-4 text-neutral-900">
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-neutral-700">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

/** iOS style frosted glass block. */
export function Card({ title, action, children }: { title?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="glass rounded-3xl p-5">
      {(title || action) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-base font-medium tracking-tight text-gray-900">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="glass rounded-3xl p-5">
      <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-gray-500">{label}</p>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-gray-900 tabular-nums">{value}</p>
      {note && <p className="mt-1 text-xs text-gray-500">{note}</p>}
    </div>
  );
}

export function Button({ variant = "primary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
}) {
  const styles = {
    primary: "bg-neutral-900 text-white hover:bg-neutral-700",
    secondary: "border border-black/10 bg-white/70 text-gray-800 hover:bg-white",
    danger: "bg-red-600 text-white hover:bg-red-500",
  }[variant];
  return (
    <button {...props} className={`rounded-full px-4 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${styles} ${className}`} />
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-gray-600 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-gray-500 mt-1">{hint}</span>}
    </label>
  );
}

const inputClass = "w-full rounded-xl border border-black/10 bg-white/70 px-3 py-2 text-sm text-gray-900 placeholder-gray-500 focus:border-violet-400 focus:outline-none focus:ring-2 focus:ring-violet-300/50";

export const Input = (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} className={inputClass} />;

export const Select = (props: SelectHTMLAttributes<HTMLSelectElement>) => <select {...props} className={inputClass} />;

export function Textarea({ value, onChange, rows = 6 }: { value: string; onChange: (v: string) => void; rows?: number }) {
  return (
    <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={rows} spellCheck={false}
      className={`${inputClass} font-mono text-xs`} />
  );
}

export function Message({ error, success }: { error?: string | null; success?: string | null }) {
  if (error) return <p role="alert" className="rounded-xl border border-red-200 bg-red-50/80 px-3 py-2 text-sm text-red-800">{error}</p>;
  if (success) return <p role="status" className="rounded-xl border border-green-200 bg-green-50/80 px-3 py-2 text-sm text-green-800">{success}</p>;
  return null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-gray-500">{children}</p>;
}

export interface Column<T> {
  label: string;
  render: (row: T) => ReactNode;
  align?: "right";
}

export function Table<T>({ columns, rows, rowKey, onRowClick, empty }: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  onRowClick?: (row: T) => void;
  empty: string;
}) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-black/10 text-left font-mono text-[11px] uppercase tracking-[0.1em] text-gray-500">
            {columns.map((c) => (
              <th key={c.label} className={`px-2 py-2 font-medium whitespace-nowrap ${c.align === "right" ? "text-right" : ""}`}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={`border-b border-black/5 ${onRowClick ? "cursor-pointer hover:bg-white/60" : ""}`}>
              {columns.map((c) => (
                <td key={c.label} className={`px-2 py-2 text-gray-800 ${c.align === "right" ? "text-right tabular-nums" : ""}`}>{c.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STATUS_STYLES: Record<string, string> = {
  active: "text-green-700 border-green-200",
  approved: "text-green-700 border-green-200",
  pending: "text-yellow-700 border-yellow-200",
  draft: "text-gray-700 border-gray-300",
  paused: "text-yellow-700 border-yellow-200",
  suspended: "text-red-700 border-red-200",
  rejected: "text-red-700 border-red-200",
};

export function StatusTag({ status }: { status: string }) {
  return (
    <span className={`inline-block rounded-full border bg-white/60 px-2 py-0.5 text-xs capitalize ${STATUS_STYLES[status] ?? "text-gray-700 border-gray-300"}`}>
      {status}
    </span>
  );
}
