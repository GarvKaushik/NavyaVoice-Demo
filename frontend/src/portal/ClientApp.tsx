/* ─────────────────────────────────────────────────────────────
 * Client portal: minutes, call results, campaigns and leads,
 * knowledge documents, test calls, opt-outs and credit requests.
 * An admin can open the same screens for any client.
 * ─────────────────────────────────────────────────────────── */
import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { del, formatDuration, formatNumber, formatTime, post, scoped, upload } from "./api";
import type { Row } from "./api";
import { Button, Card, Empty, Field, Input, Message, Select, Shell, SidebarBox, Stat, StatusTag, Table, useData, useHashRoute } from "./ui";
import type { NavItem } from "./ui";
import TestCall from "./TestCall";

const NAV: NavItem[] = [
  { key: "overview", label: "Overview", icon: "home" },
  { key: "campaigns", label: "Campaigns & leads", icon: "megaphone", group: "Calling" },
  { key: "test", label: "Test & call", icon: "phone", group: "Calling" },
  { key: "calls", label: "Call results", icon: "list", group: "Calling" },
  { key: "knowledge", label: "Knowledge", icon: "book", group: "Content" },
  { key: "opt-outs", label: "Opt-outs", icon: "ban", group: "Account" },
  { key: "credits", label: "Credits", icon: "wallet", group: "Account" },
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
  const [conclusion, setConclusion] = useState("");

  return (
    <Shell
      title={title}
      subtitle={subtitle}
      nav={NAV}
      route={route}
      onNavigate={(key) => navigate(`${prefix}${key}`)}
      onSignOut={onSignOut}
      badge={onExit && <Button variant="secondary" onClick={onExit}>Back to admin</Button>}
      footer={overview && (
        <SidebarBox label="Minutes left" value={formatNumber(overview.minutes.remaining)}
          action={<Button variant="secondary" className="w-full" onClick={() => navigate(`${prefix}credits`)}>Request more minutes</Button>} />
      )}
    >
      {overview?.minutes.out_of_minutes && (
        <Message error="You are out of minutes, so new calls are paused. Request more credits and calling resumes once they are approved." />
      )}
      {route === "overview" && (
        <Overview overview={overview} clientId={clientId} onChoose={(key) => { setConclusion(key); navigate(`${prefix}calls`); }} />
      )}
      {route === "calls" && <Calls clientId={clientId} conclusion={conclusion} setConclusion={setConclusion} />}
      {route === "campaigns" && <Campaigns clientId={clientId} />}
      {route === "knowledge" && <Knowledge clientId={clientId} />}
      {route === "test" && overview && (
        <TestCall clientId={clientId} used={overview.test_call.used_minutes} limit={overview.test_call.monthly_limit} />
      )}
      {route === "opt-outs" && <OptOuts clientId={clientId} />}
      {route === "credits" && <Credits clientId={clientId} overview={overview} onChange={reload} />}
    </Shell>
  );
}

// ── Overview ─────────────────────────────────────────────────
function Overview({ overview, clientId, onChoose }: { overview: Row | null; clientId?: string; onChoose: (key: string) => void }) {
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

      <Card title="Minutes">
        <div className="flex justify-between text-xs text-gray-400 mb-1.5">
          <span>{formatNumber(minutes.remaining)} remaining</span>
          <span>{formatNumber(minutes.total_granted)} granted in total</span>
        </div>
        <div className="h-2 rounded-full bg-gray-800 overflow-hidden" role="img"
          aria-label={`${Math.round(share * 100)} percent of minutes remaining`}>
          <div className={`h-full rounded-full ${low ? "bg-amber-400" : "bg-indigo-400"}`} style={{ width: `${share * 100}%` }} />
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
                <button onClick={() => onChoose(c.key)} className="w-full rounded px-1 py-1 text-left hover:bg-gray-800/60">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-200">{c.label}</span>
                    <span className="text-gray-200 tabular-nums">{formatNumber(c.count)}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-gray-800">
                    {c.count > 0 && <div className="h-full rounded-full bg-indigo-400" style={{ width: `${(c.count / largest) * 100}%` }} />}
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
          <p className="h-5 text-xs text-gray-300 tabular-nums">
            {hover ? `${label(hover.date)}: ${formatNumber(hover.minutes, 1)} min, ${formatNumber(hover.calls)} calls`
              : `Highest day: ${formatNumber(peak, 1)} min`}
          </p>
          <div className="mt-2 h-32 flex items-end gap-0.5 border-b border-gray-700" onMouseLeave={() => setHover(null)}>
            {rows.map((r) => (
              <div key={r.date} className="group flex-1 h-full flex items-end" onMouseEnter={() => setHover(r)}
                title={`${label(r.date)}: ${formatNumber(r.minutes, 1)} min`}>
                {r.minutes > 0 && (
                  <div className="w-full max-w-4 mx-auto rounded-t bg-indigo-400 group-hover:bg-indigo-300"
                    style={{ height: `${Math.max((r.minutes / peak) * 100, 2)}%` }} />
                )}
              </div>
            ))}
          </div>
          <div className="mt-1 flex justify-between text-xs text-gray-500">
            <span>{label(rows[0].date)}</span>
            <span>{label(rows[rows.length - 1].date)}</span>
          </div>
          <details className="mt-2 text-xs text-gray-400">
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
const CONCLUSION_FILTERS = [
  ["", "All"], ["ready", "Ready"], ["refused", "Refused"], ["contact_again", "Contact again"],
  ["manager", "Talk to manager"], ["unclear", "Unclear"], ["other", "Not reached / other"],
];
const PAGE_SIZE = 50;

function Calls({ clientId, conclusion, setConclusion }: { clientId?: string; conclusion: string; setConclusion: (c: string) => void }) {
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const query = `/client/calls?limit=${PAGE_SIZE}&offset=${offset}${conclusion ? `&conclusion=${conclusion}` : ""}`;
  const { data, error } = useData<Row>(scoped(query, clientId));

  return (
    <>
      {openId && <CallDetail callId={openId} clientId={clientId} onClose={() => setOpenId(null)} />}
      <Card>
        <div className="mb-3 flex flex-wrap gap-1">
          {CONCLUSION_FILTERS.map(([key, label]) => (
            <button key={key} onClick={() => { setConclusion(key); setOffset(0); }}
              className={`rounded-full px-3 py-1 text-xs ${conclusion === key ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-300 hover:text-white"}`}>
              {label}
            </button>
          ))}
        </div>
        <Message error={error} />
        <Table<Row>
          rows={data?.calls ?? []}
          rowKey={(r) => r.call_id}
          onRowClick={(r) => { setOpenId(r.call_id); window.scrollTo({ top: 0, behavior: "smooth" }); }}
          empty="No calls here yet."
          columns={[
            { label: "Time", render: (r) => formatTime(r.start_time) },
            { label: "Customer", render: (r) => r.customer_name },
            { label: "Number", render: (r) => r.phone_number },
            { label: "Result", render: (r) => r.conclusion_label },
            { label: "Talk time", align: "right", render: (r) => formatDuration(r.answered_seconds) },
            { label: "Minutes", align: "right", render: (r) => formatNumber(r.billed_minutes, 1) },
            { label: "Recording", render: (r) => (r.has_recording ? "Yes" : "—") },
          ]}
        />
        {data && data.total > PAGE_SIZE && (
          <div className="mt-3 flex items-center justify-between text-xs text-gray-400">
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

function CallDetail({ callId, clientId, onClose }: { callId: string; clientId?: string; onClose: () => void }) {
  const { data: call } = useData<Row>(scoped(`/client/calls/${callId}`, clientId));
  return (
    <Card title={call ? `${call.customer_name} · ${call.conclusion_label}` : "Call"} action={<Button variant="secondary" onClick={onClose}>Close</Button>}>
      {!call ? <Empty>Loading…</Empty> : (
        <div className="space-y-4">
          <p className="text-xs text-gray-400">{formatTime(call.start_time)} · {call.phone_number} · {formatDuration(call.answered_seconds)} talk time</p>
          {call.summary?.summary && (
            <div>
              <h3 className="text-xs font-semibold text-gray-400 mb-1">Summary</h3>
              <p className="text-sm text-gray-200">{call.summary.summary}</p>
              {call.summary.next_step && <p className="mt-1 text-sm text-gray-200">Next step: {call.summary.next_step}</p>}
            </div>
          )}
          {call.recording_url && <audio controls preload="none" src={call.recording_url} className="w-full" />}
          <div>
            <h3 className="text-xs font-semibold text-gray-400 mb-1">Transcript</h3>
            {call.transcript.length ? (
              <ul className="space-y-1.5 max-h-96 overflow-y-auto">
                {call.transcript.map((t: Row, i: number) => (
                  <li key={i} className="text-sm">
                    <span className={t.speaker === "agent" ? "text-indigo-300" : "text-gray-300"}>{t.speaker === "agent" ? "Agent" : "Customer"}: </span>
                    <span className="text-gray-200">{t.text}</span>
                  </li>
                ))}
              </ul>
            ) : <Empty>No conversation on this call.</Empty>}
          </div>
        </div>
      )}
    </Card>
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

function Campaigns({ clientId }: { clientId?: string }) {
  const { data: campaigns, reload } = useData<Row[]>(scoped("/client/campaigns", clientId));
  const { data: products } = useData<Row[]>(scoped("/client/products", clientId));
  const [name, setName] = useState("");
  const [sku, setSku] = useState("");
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
      await post(scoped("/client/campaigns", clientId), { name, product_skus: [sku || skus[0]] });
      setName("");
      return "Campaign created. Add leads, then start it.";
    });
  }

  return (
    <>
      <Message error={message.error} success={message.success} />
      <Card title="New campaign">
        {skus.length === 0 ? <Empty>Your products have not been set up yet. Please contact your account manager.</Empty> : (
          <form onSubmit={create} className="grid gap-3 sm:grid-cols-3 items-end">
            <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /></Field>
            <Field label="Product">
              <Select value={sku || skus[0]} onChange={(e) => setSku(e.target.value)}>
                {skus.map((s) => <option key={s}>{s}</option>)}
              </Select>
            </Field>
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
            { label: "Leads", align: "right", render: (c) => formatNumber(c.leads) },
            { label: "Waiting to dial", align: "right", render: (c) => formatNumber(c.queued) },
            { label: "", render: (c) => (
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => setUploadFor(c.id)}>Add leads</Button>
                <Button variant="secondary" onClick={() => setLeadsFor(c.id)}>View leads</Button>
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
        <p className="text-xs text-gray-400">Columns: phone_number (with country code, like +919876543210) and full_name. An optional consent_source column overrides the field below.</p>
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
        <p className="mb-3 text-sm text-gray-400">You have {formatNumber(overview?.minutes.remaining)} minutes remaining.</p>
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
