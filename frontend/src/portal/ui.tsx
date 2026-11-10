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
    <div className="min-h-screen bg-gray-950 text-gray-100 lg:flex">
      {open && <div className="fixed inset-0 z-30 bg-black/60 lg:hidden" onClick={() => setOpen(false)} aria-hidden="true" />}

      <aside className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-gray-800 bg-gray-950 transition-transform
        lg:sticky lg:top-0 lg:h-screen lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"} ${slim ? "lg:w-16" : "lg:w-64"}`}>
        <div className={`flex items-center gap-2 px-3 py-3 ${slim ? "lg:flex-col" : "justify-between"}`}>
          <Logo compact={slim} />
          <button onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="hidden lg:flex h-9 w-9 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white">
            <Icon name="panel" />
          </button>
          <button onClick={() => setOpen(false)} aria-label="Close menu"
            className="lg:hidden flex h-9 w-9 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white">
            <Icon name="close" />
          </button>
        </div>

        <div className={`mx-3 mb-2 flex items-center gap-2 rounded-lg border border-gray-800 bg-gray-900/60 p-2 ${slim ? "lg:justify-center lg:p-1.5" : ""}`}>
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-indigo-600 text-xs font-semibold text-white" aria-hidden="true">
            {(title.trim()[0] ?? "?").toUpperCase()}
          </span>
          <div className={`min-w-0 flex-1 ${slim ? "lg:hidden" : ""}`}>
            <p className="truncate text-sm font-medium text-white">{title}</p>
            <p className="truncate text-xs text-gray-400">{subtitle}</p>
          </div>
          <button onClick={onSignOut} aria-label="Sign out" title="Sign out"
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-gray-400 hover:bg-gray-800 hover:text-white ${slim ? "lg:hidden" : ""}`}>
            <Icon name="logout" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 pb-3" aria-label="Main">
          {nav.map((item, i) => (
            <div key={item.key}>
              {item.group && item.group !== nav[i - 1]?.group && (
                <p className={`mt-4 mb-1 px-2 text-[11px] font-medium uppercase tracking-wider text-gray-500 ${slim ? "lg:sr-only" : ""}`}>{item.group}</p>
              )}
              <button onClick={() => go(item.key)} title={slim ? item.label : undefined}
                aria-current={current === item.key ? "page" : undefined}
                className={`relative flex w-full items-center gap-3 rounded-lg px-2 py-2 text-sm ${slim ? "lg:justify-center" : ""} ${
                  current === item.key ? "bg-indigo-500/15 text-white ring-1 ring-inset ring-indigo-500/30" : "text-gray-300 hover:bg-gray-800/70 hover:text-white"
                }`}>
                <Icon name={item.icon} className={`h-4 w-4 ${current === item.key ? "text-indigo-300" : ""}`} />
                <span className={`flex-1 truncate text-left ${slim ? "lg:sr-only" : ""}`}>{item.label}</span>
                {item.count ? (
                  <span className={`rounded-full bg-indigo-600 px-1.5 text-xs text-white ${slim ? "lg:absolute lg:right-0.5 lg:top-0.5" : ""}`}>{item.count}</span>
                ) : null}
              </button>
            </div>
          ))}
        </nav>

        {footer && <div className={`border-t border-gray-800 p-3 ${slim ? "lg:hidden" : ""}`}>{footer}</div>}
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 border-b border-gray-800 bg-gray-950/90 backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3">
            <button onClick={() => setOpen(true)} aria-label="Open menu"
              className="lg:hidden -ml-1 flex h-9 w-9 items-center justify-center rounded-lg text-gray-300 hover:bg-gray-800">
              <Icon name="menu" className="h-5 w-5" />
            </button>
            <h1 className="min-w-0 flex-1 truncate text-base font-semibold text-white">{page}</h1>
            {badge}
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 space-y-6">{children}</main>
      </div>
    </div>
  );
}

/** The box pinned to the bottom of the sidebar. */
export function SidebarBox({ label, value, action }: { label: string; value: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="text-gray-300">{label}</span>
        <span className="font-semibold text-white tabular-nums">{value}</span>
      </div>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Card({ title, action, children }: { title?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-gray-800 bg-gray-900/60 p-4">
      {(title || action) && (
        <div className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="text-sm font-semibold text-white">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/60 p-4">
      <p className="text-xs text-gray-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-white tabular-nums">{value}</p>
      {note && <p className="mt-1 text-xs text-gray-500">{note}</p>}
    </div>
  );
}

export function Button({ variant = "primary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger";
}) {
  const styles = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-500",
    secondary: "border border-gray-700 text-gray-200 hover:bg-gray-800",
    danger: "bg-red-700 text-white hover:bg-red-600",
  }[variant];
  return (
    <button {...props} className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${styles} ${className}`} />
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-gray-400 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-gray-500 mt-1">{hint}</span>}
    </label>
  );
}

const inputClass = "w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-1.5 text-sm text-white placeholder-gray-600 focus:border-indigo-400 focus:outline-none";

export const Input = (props: InputHTMLAttributes<HTMLInputElement>) => <input {...props} className={inputClass} />;

export const Select = (props: SelectHTMLAttributes<HTMLSelectElement>) => <select {...props} className={inputClass} />;

export function Textarea({ value, onChange, rows = 6 }: { value: string; onChange: (v: string) => void; rows?: number }) {
  return (
    <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={rows} spellCheck={false}
      className={`${inputClass} font-mono text-xs`} />
  );
}

export function Message({ error, success }: { error?: string | null; success?: string | null }) {
  if (error) return <p role="alert" className="rounded-lg border border-red-800 bg-red-900/30 px-3 py-2 text-sm text-red-200">{error}</p>;
  if (success) return <p role="status" className="rounded-lg border border-green-800 bg-green-900/30 px-3 py-2 text-sm text-green-200">{success}</p>;
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
          <tr className="border-b border-gray-800 text-left text-xs text-gray-400">
            {columns.map((c) => (
              <th key={c.label} className={`px-2 py-2 font-medium whitespace-nowrap ${c.align === "right" ? "text-right" : ""}`}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={`border-b border-gray-800/60 ${onRowClick ? "cursor-pointer hover:bg-gray-800/50" : ""}`}>
              {columns.map((c) => (
                <td key={c.label} className={`px-2 py-2 text-gray-200 ${c.align === "right" ? "text-right tabular-nums" : ""}`}>{c.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STATUS_STYLES: Record<string, string> = {
  active: "text-green-300 border-green-800",
  approved: "text-green-300 border-green-800",
  pending: "text-yellow-300 border-yellow-800",
  draft: "text-gray-300 border-gray-700",
  paused: "text-yellow-300 border-yellow-800",
  suspended: "text-red-300 border-red-800",
  rejected: "text-red-300 border-red-800",
};

export function StatusTag({ status }: { status: string }) {
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-xs capitalize ${STATUS_STYLES[status] ?? "text-gray-300 border-gray-700"}`}>
      {status}
    </span>
  );
}
