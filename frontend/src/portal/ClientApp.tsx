/* ─────────────────────────────────────────────────────────────
 * Client portal: minutes, call results, campaigns and leads,
 * knowledge documents, test calls, opt-outs and credit requests.
 * An admin can open the same screens for any client.
 * ─────────────────────────────────────────────────────────── */
import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { del, formatDuration, formatNumber, formatTime, post, put, scoped, upload } from "./api";
import type { Row } from "./api";
import { Button, Card, Empty, Field, Input, Message, Select, Shell, SidebarBox, Stat, StatusTag, Table, Textarea, useData, useHashRoute } from "./ui";
import type { NavItem } from "./ui";
import TestCall from "./TestCall";
import Account from "./Account";

const NAV: NavItem[] = [
  { key: "overview", label: "Overview", icon: "home" },
  { key: "campaigns", label: "Campaigns & leads", icon: "megaphone", group: "Calling" },
  { key: "test", label: "Test & call", icon: "phone", group: "Calling" },
  { key: "calls", label: "Call results", icon: "list", group: "Calling" },
  { key: "appointments", label: "Appointments", icon: "calendar", group: "Calling" },
  { key: "knowledge", label: "Knowledge", icon: "book", group: "Content" },
  { key: "opt-outs", label: "Opt-outs", icon: "ban", group: "Account" },
  { key: "plans", label: "Plans", icon: "layers", group: "Account" },
  { key: "credits", label: "Credits", icon: "wallet", group: "Account" },
  { key: "account", label: "Account", icon: "shield", group: "Account" },
];

export default function ClientApp({ clientId, title, subtitle, onSignOut, onExit }: {
  clientId?: string;
  title: string;
  subtitle: string;
  onSignOut: () => void;
  onExit?: () => void;
}) {
  const prefix = clientId ? `client/${clientId}/` : "";
  const [hashRoute, navigate] = useHashRoute(`${prefix}overview`);
  const route = hashRoute.startsWith(prefix) ? hashRoute.slice(prefix.length) : "overview";
  const { data: overview, reload } = useData<Row>(scoped("/client/overview", clientId));
  const nav = overview?.client.is_trial
    ? [NAV[0], { key: "setup", label: "Set up your agent", icon: "panel" as const, group: "Calling" }, ...NAV.slice(1)]
    : NAV;
  const [conclusion, setConclusion] = useState("");
  const [campaign, setCampaign] = useState("");

  return (
    <Shell
      title={title}
      subtitle={subtitle}
      nav={nav}
      route={route}
      onNavigate={(key) => navigate(`${prefix}${key}`)}
      onSignOut={onSignOut}
      badge={onExit && <Button variant="secondary" onClick={onExit}>Back to admin</Button>}
      footer={overview && (overview.client.is_trial ? (
        <SidebarBox label="You're on a free trial"
          value={`${formatNumber(Math.max(0, overview.minutes.remaining), 1)} min left`}
          action={<Button className="w-full" onClick={() => navigate(`${prefix}plans`)}>See plans</Button>} />
      ) : (
        <SidebarBox label="Minutes left" value={formatNumber(overview.minutes.remaining)}
          action={<Button className="w-full" onClick={() => navigate(`${prefix}credits`)}>Request more minutes</Button>} />
      ))}
    >
      {overview?.minutes.out_of_minutes && !overview.client.is_trial && (
        <Message error="You are out of minutes, so new calls are paused. Request more credits and calling resumes once they are approved." />
      )}
      {route === "overview" && (
        <Overview overview={overview} clientId={clientId} onChoose={(key) => { setCampaign(""); setConclusion(key); navigate(`${prefix}calls`); }}
          onSeeAppointments={() => navigate(`${prefix}appointments`)} />
      )}
      {route === "calls" && (
        <Calls clientId={clientId} campaignId={campaign} setCampaignId={setCampaign} conclusion={conclusion} setConclusion={setConclusion} />
      )}
      {route === "campaigns" && (
        <Campaigns clientId={clientId} onViewCalls={(id) => { setCampaign(id); setConclusion(""); navigate(`${prefix}calls`); }} />
      )}
      {route === "appointments" && <Appointments clientId={clientId} />}
      {route === "knowledge" && <Knowledge clientId={clientId} />}
      {route === "test" && overview && (
        <TestCall clientId={clientId} used={overview.test_call.used_minutes} limit={overview.test_call.monthly_limit}
          trial={overview.client.is_trial} trialLeft={overview.minutes.remaining} onSeePlans={() => navigate(`${prefix}plans`)} />
      )}
      {route === "opt-outs" && <OptOuts clientId={clientId} />}
      {route === "credits" && <Credits clientId={clientId} overview={overview} onChange={reload} />}
      {route === "plans" && <Plans clientId={clientId} overview={overview} />}
      {route === "setup" && <AgentSetup clientId={clientId} onSaved={() => navigate(`${prefix}test`)} />}
      {route === "account" && <Account />}
    </Shell>
  );
}

// ── Overview ─────────────────────────────────────────────────
function Overview({ overview, clientId, onChoose, onSeeAppointments }: {
  overview: Row | null;
  clientId?: string;
  onChoose: (key: string) => void;
  onSeeAppointments: () => void;
}) {
  if (!overview) return <Empty>Loading…</Empty>;
  const { minutes, plan } = overview;
  const share = minutes.total_granted > 0 ? Math.max(0, Math.min(1, minutes.remaining / minutes.total_granted)) : 0;
  const low = share < 0.1;
  const conclusions: Row[] = overview.conclusions;
  const largest = Math.max(...conclusions.map((c) => c.count), 1);
  const totalCalls = conclusions.reduce((sum, c) => sum + c.count, 0);

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Minutes remaining" value={formatNumber(minutes.remaining)} note={low ? "Running low" : undefined} />
        <Stat label="Used this month" value={formatNumber(minutes.used_this_month)} note="minutes" />
        <Stat label="Calls this month" value={formatNumber(totalCalls)} />
        <Stat label="Calls in progress" value={overview.active_calls} note={`Up to ${overview.client.max_concurrent_calls} at once`} />
      </div>

      <Upcoming clientId={clientId} onSeeAll={onSeeAppointments} />

      <Card title="Minutes">
        <div className="flex justify-between text-xs text-gray-600 mb-1.5">
          <span>{formatNumber(minutes.remaining)} remaining</span>
          <span>{formatNumber(minutes.total_granted)} granted in total</span>
        </div>
        <div className="h-2 rounded-full bg-black/5 overflow-hidden" role="img"
          aria-label={`${Math.round(share * 100)} percent of minutes remaining`}>
          <div className={`h-full rounded-full ${low ? "bg-amber-600" : "bg-lime-600"}`} style={{ width: `${share * 100}%` }} />
        </div>
        {plan && (
          <p className="mt-2 text-xs text-gray-500">
            {plan.name} plan. {plan.billing_increment_seconds === 1 ? "Billed per answered minute, by the second."
              : `Answered time is rounded up to ${plan.billing_increment_seconds} seconds per call.`}
          </p>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Call results this month">
          <ul className="space-y-1">
            {conclusions.map((c) => (
              <li key={c.key}>
                <button onClick={() => onChoose(c.key)} className="w-full rounded px-1 py-1 text-left hover:bg-white/60">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-800">{c.label}</span>
                    <span className="text-gray-800 tabular-nums">{formatNumber(c.count)}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-black/5">
                    {c.count > 0 && <div className="h-full rounded-full bg-lime-600" style={{ width: `${(c.count / largest) * 100}%` }} />}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        <UsageChart clientId={clientId} />
      </div>
    </>
  );
}

/** The next few demos, meetings and callbacks, on the overview. */
function Upcoming({ clientId, onSeeAll }: { clientId?: string; onSeeAll: () => void }) {
  const { data } = useData<Row>(scoped("/client/appointments?when=upcoming&limit=5", clientId));
  const rows: Row[] = data?.appointments ?? [];
  return (
    <Card title="Upcoming" action={<Button variant="secondary" onClick={onSeeAll}>See all</Button>}>
      {rows.length ? (
        <ul className="divide-y divide-black/5">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <div className="min-w-0">
                <p className="truncate text-gray-900">{r.customer_name ?? "—"} <span className="text-xs text-gray-500 tabular-nums">{r.phone_number}</span></p>
                <p className="text-xs text-gray-600">{r.kind_label}</p>
              </div>
              <span className="whitespace-nowrap text-gray-800">{formatTime(r.at)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>No upcoming demos, meetings or callbacks.</Empty>
      )}
    </Card>
  );
}

/** Days without calls still take up a slot, so gaps in calling are visible. */
function withEmptyDays(rows: Row[]): Row[] {
  if (!rows.length) return rows;
  const byDate = new Map(rows.map((r) => [r.date, r]));
  const out: Row[] = [];
  const end = new Date(`${rows[rows.length - 1].date}T00:00:00Z`);
  for (let d = new Date(`${rows[0].date}T00:00:00Z`); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
    const date = d.toISOString().slice(0, 10);
    out.push(byDate.get(date) ?? { date, minutes: 0, calls: 0 });
  }
  return out;
}

function UsageChart({ clientId }: { clientId?: string }) {
  const { data } = useData<Row[]>(scoped("/client/usage?days=30", clientId));
  const [hover, setHover] = useState<Row | null>(null);
  const rows = withEmptyDays(data ?? []);
  const peak = Math.max(...rows.map((r) => r.minutes), 1);
  const label = (date: string) => new Date(`${date}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

  return (
    <Card title="Minutes used per day, last 30 days">
      {!rows.length ? <Empty>No calls in the last 30 days.</Empty> : (
        <>
          <p className="h-5 text-xs text-gray-700 tabular-nums">
            {hover ? `${label(hover.date)}: ${formatNumber(hover.minutes, 1)} min, ${formatNumber(hover.calls)} calls`
              : `Highest day: ${formatNumber(peak, 1)} min`}
          </p>
          <div className="mt-2 h-32 flex items-end gap-0.5 border-b border-gray-300" onMouseLeave={() => setHover(null)}>
            {rows.map((r) => (
              <div key={r.date} className="group flex-1 h-full flex items-end" onMouseEnter={() => setHover(r)}
                title={`${label(r.date)}: ${formatNumber(r.minutes, 1)} min`}>
                {r.minutes > 0 && (
                  <div className="w-full max-w-4 mx-auto rounded-t bg-lime-600 group-hover:bg-lime-700"
                    style={{ height: `${Math.max((r.minutes / peak) * 100, 2)}%` }} />
                )}
              </div>
            ))}
          </div>
          <div className="mt-1 flex justify-between text-xs text-gray-500">
            <span>{label(rows[0].date)}</span>
            <span>{label(rows[rows.length - 1].date)}</span>
          </div>
          <details className="mt-2 text-xs text-gray-600">
            <summary className="cursor-pointer">Show as table</summary>
            <table className="mt-2 w-full">
              <tbody>
                {rows.map((r) => (
                  <tr key={r.date}><td className="py-0.5">{label(r.date)}</td><td className="text-right tabular-nums">{formatNumber(r.minutes, 1)} min</td></tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </Card>
  );
}

// ── Call results ─────────────────────────────────────────────
const CONCLUSION_STYLES: Record<string, string> = {
  ready: "border-green-200 bg-green-100/40 text-green-700",
  refused: "border-red-100 bg-red-100/30 text-red-700",
  contact_again: "border-amber-100 bg-amber-100/30 text-amber-700",
  manager: "border-sky-100 bg-sky-100/30 text-sky-700",
  unclear: "border-gray-300 bg-black/5 text-gray-700",
  other: "border-white/70 bg-white text-gray-600",
};
const PAGE_SIZE = 50;

function ResultTag({ conclusion, label }: { conclusion: string; label: string }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs ${CONCLUSION_STYLES[conclusion] ?? CONCLUSION_STYLES.other}`}>
      {label}
    </span>
  );
}

const percent = (part: number, whole: number) => (whole ? `${Math.round((part / whole) * 100)}%` : "—");

/** Adds up campaign totals into one row for "All campaigns". */
function sumTotals(rows: Row[]): Row {
  const total: Row = { calls: 0, answered: 0, ready: 0, talk_seconds: 0, billed_minutes: 0 };
  for (const r of rows) for (const key of Object.keys(total)) total[key] += r[key] ?? 0;
  return total;
}

function Calls({ clientId, campaignId, setCampaignId, conclusion, setConclusion }: {
  clientId?: string;
  campaignId: string;
  setCampaignId: (id: string) => void;
  conclusion: string;
  setConclusion: (c: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const { data: campaignList } = useData<Row[]>(scoped("/client/campaigns", clientId));
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (campaignId) params.set("campaign_id", campaignId);
  if (conclusion) params.set("conclusion", conclusion);
  const { data, error } = useData<Row>(scoped(`/client/calls?${params}`, clientId));

  const totals = new Map<string, Row>((data?.campaigns ?? []).map((t: Row) => [t.campaign_id, t]));
  const names = new Map<string, string>((campaignList ?? []).map((c) => [c.id, c.name]));
  const choices = [
    { id: "", name: "All campaigns", status: "", t: sumTotals(data?.campaigns ?? []) },
    ...(campaignList ?? [])
      .map((c) => ({ id: c.id as string, name: c.name as string, status: c.status as string, t: totals.get(c.id) }))
      .sort((a, b) => (b.t?.calls ?? 0) - (a.t?.calls ?? 0)),
  ];
  const current = choices.find((c) => c.id === campaignId)?.t ?? sumTotals([]);
  const resultCounts: Row[] = data?.conclusions ?? [];
  const allResults = resultCounts.reduce((sum, c) => sum + c.count, 0);

  const chooseCampaign = (id: string) => { setCampaignId(id); setConclusion(""); setOffset(0); };
  const chooseResult = (key: string) => { setConclusion(key); setOffset(0); };
  const chip = (active: boolean) =>
    `rounded-full px-3 py-1 text-xs transition-colors ${active ? "bg-neutral-900 text-white" : "bg-black/5 text-gray-700 hover:text-gray-900"}`;

  return (
    <>
      {openId && (
        <CallDetail callId={openId} clientId={clientId} campaignNames={names} onClose={() => setOpenId(null)} />
      )}

      <div role="tablist" aria-label="Campaign" className="flex gap-2 overflow-x-auto pb-1">
        {choices.map((c) => {
          const active = c.id === campaignId;
          return (
            <button key={c.id || "all"} role="tab" aria-selected={active} onClick={() => chooseCampaign(c.id)}
              className={`w-52 shrink-0 rounded-2xl border px-4 py-3 text-left transition-colors ${
                active ? "border-neutral-900 bg-white" : "border-white/70 glass hover:border-gray-400"}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium text-gray-900" title={c.name}>{c.name}</span>
                {c.status && <StatusTag status={c.status} />}
              </div>
              <p className="mt-1 text-xs text-gray-600 tabular-nums">
                {formatNumber(c.t?.calls ?? 0)} calls · {formatNumber(c.t?.ready ?? 0)} ready
              </p>
            </button>
          );
        })}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Calls" value={formatNumber(current.calls)} />
        <Stat label="Answered" value={formatNumber(current.answered)} note={`${percent(current.answered, current.calls)} of calls`} />
        <Stat label="Ready" value={formatNumber(current.ready)} note={`${percent(current.ready, current.answered)} of answered calls`} />
        <Stat label="Talk time" value={`${formatNumber(current.talk_seconds / 60, 1)} min`}
          note={`${formatNumber(current.billed_minutes, 1)} billed minutes`} />
      </div>

      <Card title={campaignId ? `Calls in ${names.get(campaignId) ?? "this campaign"}` : "Calls in all campaigns"}>
        <div className="mb-3 flex flex-wrap gap-1">
          <button onClick={() => chooseResult("")} className={chip(conclusion === "")}>All · {formatNumber(allResults)}</button>
          {resultCounts.map((c) => (
            <button key={c.key} onClick={() => chooseResult(c.key)} className={chip(conclusion === c.key)}>
              {c.label} · {formatNumber(c.count)}
            </button>
          ))}
        </div>
        <Message error={error} />
        <Table<Row>
          rows={data?.calls ?? []}
          rowKey={(r) => r.call_id}
          onRowClick={(r) => setOpenId(r.call_id)}
          empty={campaignId ? "No finished calls in this campaign yet." : "No calls here yet."}
          columns={[
            { label: "When", render: (r) => <span className="whitespace-nowrap">{formatTime(r.start_time)}</span> },
            { label: "Customer", render: (r) => (
              <div>
                <p className="text-gray-900">{r.customer_name}</p>
                <p className="text-xs text-gray-500 tabular-nums">{r.phone_number}</p>
              </div>
            ) },
            ...(campaignId ? [] : [{ label: "Campaign", render: (r: Row) => names.get(r.campaign_id) ?? "—" }]),
            { label: "Result", render: (r) => <ResultTag conclusion={r.conclusion} label={r.conclusion_label} /> },
            { label: "Talk time", align: "right" as const, render: (r) => (r.answered ? formatDuration(r.answered_seconds) : "—") },
            { label: "Minutes", align: "right" as const, render: (r) => formatNumber(r.billed_minutes, 1) },
            { label: "Recording", render: (r) => (r.has_recording ? "Yes" : "—") },
          ]}
        />
        {data && data.total > PAGE_SIZE && (
          <div className="mt-3 flex items-center justify-between text-xs text-gray-600">
            <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {formatNumber(data.total)}</span>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(offset - PAGE_SIZE)}>Previous</Button>
              <Button variant="secondary" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</Button>
            </div>
          </div>
        )}
      </Card>
    </>
  );
}

/** One call in a side panel: details, summary, recording and the conversation. */
function CallDetail({ callId, clientId, campaignNames, onClose }: {
  callId: string;
  clientId?: string;
  campaignNames: Map<string, string>;
  onClose: () => void;
}) {
  const { data: call } = useData<Row>(scoped(`/client/calls/${callId}`, clientId));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const facts: [string, ReactNode][] = call ? [
    ["Campaign", campaignNames.get(call.campaign_id) ?? "—"],
    ["When", formatTime(call.start_time)],
    ["Number", call.phone_number],
    ["Talk time", call.answered ? formatDuration(call.answered_seconds) : "Not answered"],
    ["Billed", `${formatNumber(call.billed_minutes, 1)} min`],
  ] : [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Call details">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} aria-hidden="true" />
      <aside className="relative flex h-full w-full max-w-xl flex-col border-l border-white/70 bg-white/70 shadow-2xl backdrop-blur-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-white/70 p-5">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-gray-900">{call?.customer_name ?? "Call"}</h2>
            {call && <div className="mt-1"><ResultTag conclusion={call.conclusion} label={call.conclusion_label} /></div>}
          </div>
          <Button variant="secondary" onClick={onClose} autoFocus>Close</Button>
        </header>

        {!call ? <Empty>Loading…</Empty> : (
          <div className="flex-1 space-y-5 overflow-y-auto p-5">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
              {facts.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-gray-500">{label}</dt>
                  <dd className="text-gray-800">{value}</dd>
                </div>
              ))}
            </dl>

            {call.summary?.summary && (
              <section className="glass rounded-2xl p-4">
                <h3 className="mb-1 text-xs font-semibold text-gray-600">Summary</h3>
                <p className="text-sm text-gray-800">{call.summary.summary}</p>
                {call.summary.next_step && <p className="mt-2 text-sm text-gray-800"><span className="text-gray-600">Next step: </span>{call.summary.next_step}</p>}
              </section>
            )}

            {call.recording_url && (
              <section>
                <h3 className="mb-1 text-xs font-semibold text-gray-600">Recording</h3>
                <audio controls preload="none" src={call.recording_url} className="w-full" />
              </section>
            )}

            <section>
              <h3 className="mb-2 text-xs font-semibold text-gray-600">Conversation</h3>
              {call.transcript.length ? (
                <ul className="space-y-2">
                  {call.transcript.map((t: Row, i: number) => {
                    const agent = t.speaker === "agent";
                    return (
                      <li key={i} className={`flex ${agent ? "justify-start" : "justify-end"}`}>
                        <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${
                          agent ? "rounded-bl-sm bg-violet-100 text-gray-900" : "rounded-br-sm bg-white text-gray-900"}`}>
                          <p className={`mb-0.5 text-[11px] ${agent ? "text-violet-700" : "text-gray-600"}`}>{agent ? "Agent" : "Customer"}</p>
                          {t.text}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              ) : <Empty>No conversation on this call.</Empty>}
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}

// ── Campaigns and leads ──────────────────────────────────────
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let cell = "", row: string[] = [], quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") { row.push(cell); cell = ""; }
    else if (ch === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
    else if (ch !== "\r") cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.some((c) => c.trim()));
}

function leadsFromCsv(text: string, consent: string): Row[] {
  const [header, ...rows] = parseCsv(text.replace(/^﻿/, ""));
  const names = (header ?? []).map((h) => h.trim().toLowerCase());
  const phone = names.findIndex((n) => ["phone_number", "phone", "number", "mobile"].includes(n));
  const name = names.findIndex((n) => ["full_name", "name", "customer"].includes(n));
  const source = names.indexOf("consent_source");
  if (phone < 0 || name < 0) throw new Error("The file needs a phone_number column and a full_name column.");
  return rows.map((r) => ({
    phone_number: (r[phone] ?? "").trim(),
    full_name: (r[name] ?? "").trim() || "ji",
    consent_source: (source >= 0 ? r[source]?.trim() : "") || consent,
  }));
}

function Campaigns({ clientId, onViewCalls }: { clientId?: string; onViewCalls: (campaignId: string) => void }) {
  const { data: campaigns, reload } = useData<Row[]>(scoped("/client/campaigns", clientId));
  const { data: products } = useData<Row[]>(scoped("/client/products", clientId));
  const [name, setName] = useState("");
  const [sku, setSku] = useState("");
  const [language, setLanguage] = useState("hinglish");
  const [uploadFor, setUploadFor] = useState<string | null>(null);
  const [leadsFor, setLeadsFor] = useState<string | null>(null);
  const [message, setMessage] = useState<{ error?: string; success?: string }>({});
  const skus = [...new Set((products ?? []).map((p) => p.sku))];

  async function act(fn: () => Promise<string>) {
    try {
      setMessage({ success: await fn() });
      await reload();
    } catch (e) {
      setMessage({ error: (e as Error).message });
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    void act(async () => {
      await post(scoped("/client/campaigns", clientId), { name, product_skus: [sku || skus[0]], language });
      setName("");
      return "Campaign created. Add leads, then start it.";
    });
  }

  return (
    <>
      <Message error={message.error} success={message.success} />
      <Card title="New campaign">
        {skus.length === 0 ? <Empty>Your products have not been set up yet. Please contact your account manager.</Empty> : (
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-4 items-end">
            <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /></Field>
            <Field label="Product">
              <Select value={sku || skus[0]} onChange={(e) => setSku(e.target.value)}>
                {skus.map((s) => <option key={s}>{s}</option>)}
              </Select>
            </Field>
            <Field label="Language"><LanguageSelect value={language} onChange={setLanguage} /></Field>
            <Button type="submit" className="justify-self-start">Create campaign</Button>
          </form>
        )}
      </Card>
      <Card title="Campaigns">
        <Table<Row>
          rows={campaigns ?? []}
          rowKey={(c) => c.id}
          empty="No campaigns yet."
          columns={[
            { label: "Name", render: (c) => c.name },
            { label: "Status", render: (c) => <StatusTag status={c.status} /> },
            { label: "Language", render: (c) => (
              <LanguageSelect value={c.language} onChange={(value) => act(async () => {
                await put(scoped(`/client/campaigns/${c.id}/language`, clientId), { language: value });
                return "Language changed. It applies from the next call.";
              })} />
            ) },
            { label: "Leads", align: "right", render: (c) => formatNumber(c.leads) },
            { label: "Waiting to dial", align: "right", render: (c) => formatNumber(c.queued) },
            { label: "", render: (c) => (
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => setUploadFor(c.id)}>Add leads</Button>
                <Button variant="secondary" onClick={() => setLeadsFor(c.id)}>View leads</Button>
                <Button variant="secondary" onClick={() => onViewCalls(c.id)}>View calls</Button>
                {c.status === "active" ? (
                  <Button variant="secondary" onClick={() => act(async () => {
                    await post(scoped(`/client/campaigns/${c.id}/pause`, clientId));
                    return "Campaign paused. Calls in progress will finish.";
                  })}>Pause</Button>
                ) : (
                  <Button onClick={() => act(async () => {
                    const r = await post(scoped(`/client/campaigns/${c.id}/start`, clientId));
                    return `Campaign started. ${formatNumber(r.queued)} new leads queued.`;
                  })}>Start</Button>
                )}
              </div>
            ) },
          ]}
        />
      </Card>
      {uploadFor && <LeadUpload campaignId={uploadFor} clientId={clientId} onDone={(text) => { setUploadFor(null); setMessage({ success: text }); void reload(); }} onClose={() => setUploadFor(null)} />}
      {leadsFor && <Leads campaignId={leadsFor} clientId={clientId} onClose={() => setLeadsFor(null)} />}
    </>
  );
}

function LanguageSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Call language">
      <option value="hinglish">Hindi</option>
      <option value="english">English</option>
    </Select>
  );
}

function LeadUpload({ campaignId, clientId, onDone, onClose }: { campaignId: string; clientId?: string; onDone: (m: string) => void; onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    try {
      const leads = leadsFromCsv(await file.text(), consent);
      if (leads.length > 5000) throw new Error("Upload up to 5,000 leads at a time.");
      const r = await post(scoped(`/client/campaigns/${campaignId}/leads`, clientId), { leads });
      onDone(`${formatNumber(r.imported_leads)} leads added, ${formatNumber(r.skipped_existing)} already on your list, ${formatNumber(r.invalid)} invalid.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Add leads from a CSV file" action={<Button variant="secondary" onClick={onClose}>Close</Button>}>
      <form onSubmit={submit} className="space-y-3 max-w-xl">
        <p className="text-xs text-gray-600">Columns: phone_number (with country code, like +919876543210) and full_name. An optional consent_source column overrides the field below.</p>
        <Field label="CSV file"><Input type="file" accept=".csv" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
        <Field label="Where did these people give consent to be called?" hint="Required by law for every lead. Example: Website form, Nov 2026.">
          <Input value={consent} onChange={(e) => setConsent(e.target.value)} required minLength={3} />
        </Field>
        <Message error={error} />
        <Button type="submit" disabled={busy || !file}>{busy ? "Uploading…" : "Upload leads"}</Button>
      </form>
    </Card>
  );
}

function Leads({ campaignId, clientId, onClose }: { campaignId: string; clientId?: string; onClose: () => void }) {
  const { data } = useData<Row>(scoped(`/client/leads?campaign_id=${campaignId}&limit=200`, clientId));
  return (
    <Card title={`Leads${data ? ` (${formatNumber(data.total)})` : ""}`} action={<Button variant="secondary" onClick={onClose}>Close</Button>}>
      <Table<Row>
        rows={data?.leads ?? []}
        rowKey={(l) => l.id}
        empty="No leads in this campaign yet."
        columns={[
          { label: "Name", render: (l) => l.full_name },
          { label: "Number", render: (l) => l.phone_number },
          { label: "Status", render: (l) => l.status.replace("_", " ") },
          { label: "Attempts", align: "right", render: (l) => l.attempts },
          { label: "Last result", render: (l) => l.conclusion_label ?? "—" },
          { label: "Next try", render: (l) => formatTime(l.next_attempt_at) },
        ]}
      />
    </Card>
  );
}

// ── Knowledge ────────────────────────────────────────────────
function Knowledge({ clientId }: { clientId?: string }) {
  const { data, reload } = useData<Row[]>(scoped("/client/knowledge", clientId));
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState<{ error?: string; success?: string }>({});

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const doc = await upload(scoped("/client/knowledge", clientId), form);
      setMessage({ success: `${doc.filename} uploaded. It will be used on calls after our team approves it.` });
      setFile(null);
      await reload();
    } catch (err) {
      setMessage({ error: (err as Error).message });
    }
  }

  return (
    <>
      <Message error={message.error} success={message.success} />
      <Card title="Add a document">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <div className="min-w-64"><Field label="File" hint=".txt, .md, .csv or .pdf, up to 5 MB. A CSV can have question and answer columns."><Input type="file" accept=".txt,.md,.csv,.pdf" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field></div>
          <Button type="submit" disabled={!file}>Upload</Button>
        </form>
      </Card>
      <Card title="Documents">
        <Table<Row>
          rows={data ?? []}
          rowKey={(d) => d.id}
          empty="No documents yet. Add your FAQs or product details so the agent can answer more questions."
          columns={[
            { label: "File", render: (d) => d.filename },
            { label: "Sections", align: "right", render: (d) => d.chunks },
            { label: "Status", render: (d) => <StatusTag status={d.status} /> },
            { label: "Uploaded", render: (d) => formatTime(d.created_at) },
            { label: "", render: (d) => (
              <div className="flex justify-end">
                <Button variant="secondary" onClick={async () => { await del(scoped(`/client/knowledge/${d.id}`, clientId)); await reload(); }}>Remove</Button>
              </div>
            ) },
          ]}
        />
      </Card>
    </>
  );
}

// ── Opt-outs ─────────────────────────────────────────────────
function OptOuts({ clientId }: { clientId?: string }) {
  const { data, reload } = useData<Row[]>(scoped("/client/opt-outs", clientId));
  const [phone, setPhone] = useState("");
  const [message, setMessage] = useState<{ error?: string; success?: string }>({});

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      await post(scoped("/client/opt-outs", clientId), { phone_number: phone.trim() });
      setMessage({ success: `${phone} will not be called again on your account.` });
      setPhone("");
      await reload();
    } catch (err) {
      setMessage({ error: (err as Error).message });
    }
  }

  return (
    <>
      <Message error={message.error} success={message.success} />
      <Card title="Add a number">
        <form onSubmit={add} className="flex flex-wrap items-end gap-3">
          <Field label="Phone number"><Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+919876543210" required /></Field>
          <Button type="submit">Block number</Button>
        </form>
      </Card>
      <Card title="Numbers that will not be called">
        <Table<Row>
          rows={data ?? []}
          rowKey={(r) => r.phone_number}
          empty="No opt-outs yet."
          columns={[
            { label: "Number", render: (r) => r.phone_number },
            { label: "Source", render: (r) => r.source.replace("_", " ") },
            { label: "Added", render: (r) => formatTime(r.at) },
          ]}
        />
      </Card>
    </>
  );
}

// ── Appointments ─────────────────────────────────────────────
function Appointments({ clientId }: { clientId?: string }) {
  const [campaignId, setCampaignId] = useState("");
  const [kind, setKind] = useState("");
  const [when, setWhen] = useState("upcoming");
  const { data: campaignList } = useData<Row[]>(scoped("/client/campaigns", clientId));
  const params = new URLSearchParams({ when });
  if (campaignId) params.set("campaign_id", campaignId);
  if (kind) params.set("kind", kind);
  const { data, error } = useData<Row>(scoped(`/client/appointments?${params}`, clientId));
  const names = new Map<string, string>((campaignList ?? []).map((c) => [c.id, c.name]));

  return (
    <Card title="Demos, meetings and callbacks">
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Field label="Campaign">
          <Select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
            <option value="">All campaigns</option>
            {(campaignList ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        </Field>
        <Field label="Type">
          <Select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All</option>
            <option value="demo">Demos</option>
            <option value="human_expert_callback">Meetings</option>
            <option value="callback">Callbacks</option>
          </Select>
        </Field>
        <Field label="When">
          <Select value={when} onChange={(e) => setWhen(e.target.value)}>
            <option value="upcoming">Upcoming</option>
            <option value="past">Past</option>
            <option value="all">All</option>
          </Select>
        </Field>
      </div>
      <Message error={error} />
      <Table<Row>
        rows={data?.appointments ?? []}
        rowKey={(r) => r.id}
        empty={when === "upcoming" ? "No upcoming demos, meetings or callbacks." : "No demos, meetings or callbacks here."}
        columns={[
          { label: "When", render: (r) => <span className="whitespace-nowrap">{formatTime(r.at)}</span> },
          { label: "Type", render: (r) => r.kind_label },
          { label: "Customer", render: (r) => (
            <div>
              <p className="text-gray-900">{r.customer_name ?? "—"}</p>
              <p className="text-xs text-gray-500 tabular-nums">{r.phone_number}</p>
            </div>
          ) },
          ...(campaignId ? [] : [{ label: "Campaign", render: (r: Row) => names.get(r.campaign_id) ?? "—" }]),
          { label: "Status", render: (r) => r.status },
        ]}
      />
    </Card>
  );
}

// ── Agent setup (free trial) ─────────────────────────────────
type SetupProduct = { name: string; price_inr: string; billing_period: string; description: string; features: string };
const blankProduct: SetupProduct = { name: "", price_inr: "", billing_period: "monthly", description: "", features: "" };

function AgentSetup({ clientId, onSaved }: { clientId?: string; onSaved: () => void }) {
  const { data } = useData<Row>(scoped("/client/setup", clientId));
  const [company, setCompany] = useState("");
  const [about, setAbout] = useState("");
  const [products, setProducts] = useState<SetupProduct[]>([blankProduct]);
  const [feedback, setFeedback] = useState<{ error?: string; success?: string }>({});
  const [doc, setDoc] = useState<File | null>(null);
  const [reading, setReading] = useState(false);

  useEffect(() => {
    if (!data) return;
    setCompany(data.company_name.includes("@") ? "" : data.company_name);
    setAbout(data.about ?? "");
    if (data.products.length) {
      setProducts(data.products.map((p: Row) => ({ name: p.name, price_inr: String(p.price_inr), billing_period: p.billing_period,
        description: p.description, features: (p.features ?? []).join("\n") })));
    }
  }, [data]);

  if (!data) return <Empty>Loading…</Empty>;
  const change = (i: number, key: keyof SetupProduct, value: string) =>
    setProducts(products.map((p, j) => (j === i ? { ...p, [key]: value } : p)));

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await put(scoped("/client/setup", clientId), {
        company_name: company, about,
        products: products.map((p) => ({ name: p.name, price_inr: Number(p.price_inr), billing_period: p.billing_period,
          description: p.description, features: p.features.split("\n").map((f) => f.trim()).filter(Boolean) })),
      });
      setFeedback({ success: "Saved. Your agent uses this from the next test call." });
      onSaved();
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    }
  }

  async function fillFromDocument() {
    if (!doc) return;
    const form = new FormData();
    form.append("file", doc);
    setReading(true);
    try {
      const draft = await upload(scoped("/client/setup/from-document", clientId), form);
      if (draft.company_name) setCompany(draft.company_name);
      if (draft.about) setAbout(draft.about);
      if (draft.products.length) {
        setProducts(draft.products.map((p: Row) => ({ name: p.name, price_inr: p.price_inr == null ? "" : String(p.price_inr),
          billing_period: p.billing_period, description: p.description, features: p.features.join("\n") })));
      }
      setFeedback({ success: `Filled in from ${doc.name}. Check every field, especially prices, then save.` });
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    } finally {
      setReading(false);
    }
  }

  if (!data.editable) {
    return <Card title="Your agent"><Empty>Your agent's content is managed by our team. Contact us to change it.</Empty></Card>;
  }
  return (
    <form onSubmit={save} className="space-y-4">
      <Message error={feedback.error} success={feedback.success} />
      <Card title="Fill from a document">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64"><Field label="File" hint="A brochure, price list or website text: .txt, .md, .csv or .pdf, up to 5 MB. We fill in the form below; nothing is saved until you press Save."><Input type="file" accept=".txt,.md,.csv,.pdf" onChange={(e) => setDoc(e.target.files?.[0] ?? null)} /></Field></div>
          <Button type="button" variant="secondary" disabled={!doc || reading} onClick={fillFromDocument}>{reading ? "Reading…" : "Fill in the form"}</Button>
        </div>
      </Card>
      <Card title="Your company">
        <div className="grid gap-3">
          <Field label="Company name" hint="Your agent says it on every call"><Input value={company} onChange={(e) => setCompany(e.target.value)} required minLength={2} /></Field>
          <Field label="About the company" hint="2-3 lines on what you do and for whom. The agent uses it to introduce you.">
            <Textarea value={about} onChange={setAbout} rows={4} />
          </Field>
        </div>
      </Card>
      {products.map((p, i) => (
        <Card key={i} title={`Product ${i + 1}`} action={products.length > 1 && (
          <Button type="button" variant="secondary" onClick={() => setProducts(products.filter((_, j) => j !== i))}>Remove</Button>
        )}>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Name"><Input value={p.name} onChange={(e) => change(i, "name", e.target.value)} required /></Field>
            <Field label="Price (₹)"><Input type="number" min={0} value={p.price_inr} onChange={(e) => change(i, "price_inr", e.target.value)} required /></Field>
            <Field label="Billing">
              <Select value={p.billing_period} onChange={(e) => change(i, "billing_period", e.target.value)}>
                <option value="monthly">Per month</option>
                <option value="annual">Per year</option>
                <option value="one_time">One-time</option>
              </Select>
            </Field>
            <div className="sm:col-span-3">
              <Field label="What it does" hint="One or two sentences, in simple words"><Textarea value={p.description} onChange={(v) => change(i, "description", v)} rows={3} /></Field>
            </div>
            <div className="sm:col-span-3">
              <Field label="Key features" hint="One per line"><Textarea value={p.features} onChange={(v) => change(i, "features", v)} rows={3} /></Field>
            </div>
          </div>
        </Card>
      ))}
      <div className="flex flex-wrap gap-2">
        {products.length < 10 && <Button type="button" variant="secondary" onClick={() => setProducts([...products, blankProduct])}>Add another product</Button>}
        <Button type="submit">Save and test my agent</Button>
      </div>
    </form>
  );
}

// ── Plans ────────────────────────────────────────────────────
function Plans({ clientId, overview }: { clientId?: string; overview: Row | null }) {
  const { data, reload } = useData<Row>(scoped("/client/plans", clientId));
  const [feedback, setFeedback] = useState<{ error?: string; success?: string }>({});

  async function ask(planName?: string) {
    try {
      await post(scoped("/client/enquiries", clientId), { plan_name: planName ?? null });
      setFeedback({ success: "Thanks! Our team will contact you about pricing and billing within one working day." });
      await reload();
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    }
  }

  if (!data || !overview) return <Empty>Loading…</Empty>;
  const current = data.plans.find((p: Row) => p.id === data.current_plan_id);
  return (
    <>
      <Message error={feedback.error} success={feedback.success} />
      <Card title="Your plan">
        {data.is_trial ? (
          <p className="text-sm text-gray-700">
            You're on a free trial with {formatNumber(Math.max(0, overview.minutes.remaining), 1)} minutes left for browser test
            calls. Phone calls to your leads start once you buy a plan.
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Plan" value={current?.name ?? overview.plan?.name ?? "—"} />
            <Stat label="Minutes left" value={formatNumber(overview.minutes.remaining)} />
            <Stat label="Used this month" value={formatNumber(overview.minutes.used_this_month)} note="minutes" />
          </div>
        )}
      </Card>
      <Card title="Plans" action={<Button onClick={() => ask()}>Talk to us about pricing</Button>}>
        {data.pricing_enquiry_open && <p className="mb-3 text-sm text-gray-600">We have your pricing request and will be in touch soon.</p>}
        {data.plans.length ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.plans.map((p: Row) => (
              <div key={p.id} className={`lift rounded-3xl border p-5 ${p.id === data.current_plan_id ? "border-neutral-900 bg-white" : "border-black/10 bg-white/60"}`}>
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-medium text-gray-900">{p.name}</h3>
                  {p.id === data.current_plan_id && <StatusTag status="active" />}
                </div>
                <p className="mt-2 text-2xl font-semibold tracking-tight tabular-nums">{formatNumber(p.included_minutes)}</p>
                <p className="text-xs text-gray-600">minutes included</p>
                {p.id !== data.current_plan_id && (
                  <Button variant="secondary" className="mt-4" onClick={() => ask(p.name)}>Ask about this plan</Button>
                )}
              </div>
            ))}
          </div>
        ) : <Empty>Plans are shared by our team. Ask us about pricing.</Empty>}
        <p className="mt-4 text-xs text-gray-500">Prices and billing are agreed with our team. More minutes are added once payment is confirmed.</p>
      </Card>
    </>
  );
}

// ── Credits ──────────────────────────────────────────────────
function Credits({ clientId, overview, onChange }: { clientId?: string; overview: Row | null; onChange: () => Promise<void> }) {
  const { data: requests, reload } = useData<Row[]>(scoped("/client/credit-requests", clientId));
  const { data: ledger } = useData<Row[]>(scoped("/client/ledger", clientId));
  const [minutes, setMinutes] = useState("");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<{ error?: string; success?: string }>({});

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await post(scoped("/client/credit-requests", clientId), { minutes: Number(minutes), note: note || null });
      setMessage({ success: "Request sent. Your minutes are added as soon as it is approved." });
      setMinutes("");
      setNote("");
      await reload();
      await onChange();
    } catch (err) {
      setMessage({ error: (err as Error).message });
    }
  }

  const history: ReactNode = (
    <Table<Row>
      rows={ledger ?? []}
      rowKey={(r) => `${r.at}-${r.minutes}`}
      empty="No minutes added yet."
      columns={[
        { label: "Date", render: (r) => formatTime(r.at) },
        { label: "Type", render: (r) => r.type.replace("_", " ") },
        { label: "Minutes", align: "right", render: (r) => formatNumber(r.minutes) },
        { label: "Balance after", align: "right", render: (r) => formatNumber(r.balance_after) },
        { label: "Note", render: (r) => r.note ?? "—" },
      ]}
    />
  );

  return (
    <>
      <Message error={message.error} success={message.success} />
      <Card title="Request more minutes">
        <p className="mb-3 text-sm text-gray-600">You have {formatNumber(overview?.minutes.remaining)} minutes remaining.</p>
        <form onSubmit={submit} className="grid gap-3 sm:grid-cols-3 items-end">
          <Field label="Minutes"><Input type="number" min={1} value={minutes} onChange={(e) => setMinutes(e.target.value)} required /></Field>
          <Field label="Note (optional)"><Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={512} /></Field>
          <Button type="submit" className="justify-self-start">Send request</Button>
        </form>
      </Card>
      <Card title="Your requests">
        <Table<Row>
          rows={requests ?? []}
          rowKey={(r) => r.id}
          empty="No requests yet."
          columns={[
            { label: "Requested", render: (r) => formatTime(r.created_at) },
            { label: "Minutes", align: "right", render: (r) => formatNumber(r.minutes) },
            { label: "Status", render: (r) => <StatusTag status={r.status} /> },
            { label: "Decided", render: (r) => formatTime(r.decided_at) },
          ]}
        />
      </Card>
      <Card title="Minutes added">{history}</Card>
    </>
  );
}
