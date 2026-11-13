/* ─────────────────────────────────────────────────────────────
 * Admin console: clients, usage, remaining minutes, plans,
 * assigned numbers, credit approvals, knowledge approvals, audit.
 * ─────────────────────────────────────────────────────────── */
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { formatInr, formatNumber, formatTime, get, patch, post, put } from "./api";
import type { Row } from "./api";
import { Button, Card, Empty, Field, Input, Message, Select, Shell, SidebarBox, Stat, StatusTag, Table, Textarea, useData, useHashRoute } from "./ui";
import type { NavItem } from "./ui";
import Account from "./Account";

type Feedback = { error?: string; success?: string };

export default function AdminApp({ email, onSignOut, onOpenClient }: {
  email: string;
  onSignOut: () => void;
  onOpenClient: (client: { id: string; name: string }) => void;
}) {
  const [route, navigate] = useHashRoute("overview");
  const { data: overview, reload } = useData<Row>("/admin/overview");
  const pendingCredits = overview?.totals.pending_credit_requests ?? 0;
  const pendingDocuments = overview?.totals.pending_documents ?? 0;
  const newEnquiries = overview?.totals.new_enquiries ?? 0;
  const nav: NavItem[] = [
    { key: "overview", label: "Overview", icon: "home" },
    { key: "enquiries", label: "Enquiries", icon: "megaphone", group: "Clients", count: newEnquiries },
    { key: "clients", label: "Add client", icon: "userPlus", group: "Clients" },
    { key: "plans", label: "Plans", icon: "layers", group: "Clients" },
    { key: "credits", label: "Credit requests", icon: "coins", group: "Approvals", count: pendingCredits },
    { key: "knowledge", label: "Knowledge approvals", icon: "fileCheck", group: "Approvals", count: pendingDocuments },
    { key: "console", label: "Script console", icon: "terminal", group: "Tools" },
    { key: "audit", label: "Audit log", icon: "scroll", group: "Tools" },
    { key: "account", label: "Account", icon: "shield", group: "Tools" },
  ];
  const [section, detail] = route.split("/");
  const page = section === "client-admin" ? "overview" : route;
  const waiting = pendingCredits + pendingDocuments;

  return (
    <Shell title="Navix AI Admin" subtitle={email} nav={nav} route={page} onNavigate={navigate} onSignOut={onSignOut}
      footer={overview && (
        <SidebarBox label="Waiting for approval" value={formatNumber(waiting)}
          action={waiting > 0 && (
            <Button className="w-full" onClick={() => navigate(pendingCredits ? "credits" : "knowledge")}>Review</Button>
          )} />
      )}>
      {section === "overview" && <Overview overview={overview} onSelect={(id) => navigate(`client-admin/${id}`)} />}
      {section === "client-admin" && detail && <ClientDetail clientId={detail} onChange={reload} onOpenClient={onOpenClient} />}
      {section === "clients" && <NewClient onCreated={(id) => { void reload(); navigate(`client-admin/${id}`); }} />}
      {section === "plans" && <Plans />}
      {section === "credits" && <CreditRequests onChange={reload} />}
      {section === "knowledge" && <KnowledgeReview onChange={reload} />}
      {section === "audit" && <Audit />}
      {section === "enquiries" && <Enquiries onChange={reload} />}
      {section === "account" && <Account email={email} />}
    </Shell>
  );
}

// ── Overview ─────────────────────────────────────────────────
function Overview({ overview, onSelect }: { overview: Row | null; onSelect: (id: string) => void }) {
  if (!overview) return <Empty>Loading…</Empty>;
  const t = overview.totals;
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Active clients" value={`${t.active_clients} of ${t.clients}`} />
        <Stat label="Calls in progress" value={`${t.active_calls} of ${t.capacity}`} />
        <Stat label="Minutes used this month" value={formatNumber(t.used_minutes_this_month)} />
        <Stat label="Minutes remaining, all clients" value={formatNumber(t.remaining_minutes)} />
        <Stat label="Revenue this month" value={formatInr(t.revenue_this_month_inr)} note="Minutes used × plan rate" />
        <Stat label="Recorded call cost this month" value={formatInr(t.cost_this_month_inr)} note="From the rates in settings; excludes fixed costs" />
      </div>
      <Card title="Clients">
        <Table<Row>
          rows={overview.clients}
          rowKey={(c) => c.id}
          onRowClick={(c) => onSelect(c.id)}
          empty="No clients yet."
          columns={[
            { label: "Client", render: (c) => c.name },
            { label: "Status", render: (c) => <StatusTag status={c.status} /> },
            { label: "Plan", render: (c) => c.plan_name ?? "—" },
            { label: "Number", render: (c) => c.caller_id ?? "Not assigned" },
            { label: "Used this month", align: "right", render: (c) => formatNumber(c.used_minutes_this_month) },
            { label: "Remaining", align: "right", render: (c) => formatNumber(c.remaining_minutes) },
            { label: "Live calls", align: "right", render: (c) => `${c.active_calls} / ${c.max_concurrent_calls}` },
            { label: "Revenue", align: "right", render: (c) => formatInr(c.revenue_this_month_inr) },
            { label: "Call cost", align: "right", render: (c) => formatInr(c.cost_this_month_inr) },
          ]}
        />
      </Card>
    </>
  );
}

// ── New client ───────────────────────────────────────────────
function NewClient({ onCreated }: { onCreated: (id: string) => void }) {
  const { data: plans } = useData<Row[]>("/admin/plans");
  const [form, setForm] = useState({ name: "", company_name: "", plan_id: "", initial_minutes: "", caller_id: "" });
  const [trial, setTrial] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      const created = await post("/admin/clients", {
        name: form.name, company_name: form.company_name, plan_id: form.plan_id || null,
        initial_minutes: form.initial_minutes === "" ? null : Number(form.initial_minutes), caller_id: form.caller_id || null,
        is_trial: trial,
      });
      onCreated(created.id);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Card title="Add a client">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2 max-w-3xl">
        <Field label="Client name"><Input value={form.name} onChange={set("name")} required minLength={2} /></Field>
        <Field label="Company name callers hear" hint="Spoken in the opening disclosure"><Input value={form.company_name} onChange={set("company_name")} required minLength={2} /></Field>
        <Field label="Plan">
          <Select value={form.plan_id} onChange={set("plan_id")}>
            <option value="">No plan</option>
            {plans?.filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.name} · ₹{p.rate_per_minute_inr}/min · {formatNumber(p.included_minutes)} min</option>)}
          </Select>
        </Field>
        <Field label="Starting minutes" hint="Leave empty to use the plan's minutes"><Input type="number" min={0} value={form.initial_minutes} onChange={set("initial_minutes")} /></Field>
        <Field label="Assigned phone number" hint="Each number can belong to one client. It must also be on the Plivo trunk."><Input value={form.caller_id} onChange={set("caller_id")} placeholder="+911401234567" /></Field>
        <label className="flex items-center gap-2 text-sm text-gray-800 sm:col-span-2">
          <input type="checkbox" checked={trial} onChange={(e) => setTrial(e.target.checked)} />
          Free trial: 50 browser test minutes, no phone calls until they buy a plan
        </label>
        <div className="sm:col-span-2"><Message error={error} /></div>
        <div className="sm:col-span-2"><Button type="submit">Create client</Button></div>
      </form>
    </Card>
  );
}

// ── Client detail ────────────────────────────────────────────
function ClientDetail({ clientId, onChange, onOpenClient }: { clientId: string; onChange: () => Promise<void>; onOpenClient: (c: { id: string; name: string }) => void }) {
  const { data: client, reload, error } = useData<Row>(`/admin/clients/${clientId}`);
  const { data: plans } = useData<Row[]>("/admin/plans");
  const [feedback, setFeedback] = useState<Feedback>({});
  const [form, setForm] = useState<Row | null>(null);
  const [minutes, setMinutes] = useState("");
  const [minutesNote, setMinutesNote] = useState("");
  const [email, setEmail] = useState("");

  useEffect(() => {
    if (client) setForm({
      name: client.name, company_name: client.company_name, status: client.status, plan_id: client.plan_id ?? "",
      caller_id: client.caller_id ?? "", voice_id: client.voice_id ?? "", agent_persona_name: client.agent_persona_name ?? "",
      complaint_contact: client.complaint_contact ?? "", max_concurrent_calls: client.max_concurrent_calls,
      transfer_numbers: (client.transfer_numbers ?? []).join(", "), payment_links_enabled: client.payment_links_enabled,
      is_trial: client.is_trial,
      crm_webhook_url: client.crm_webhook_url ?? "", crm_webhook_secret: "",
    });
  }, [client]);

  if (error) return <Message error={error} />;
  if (!client || !form) return <Empty>Loading…</Empty>;
  const set = (key: string) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  async function run(fn: () => Promise<string>) {
    try {
      setFeedback({ success: await fn() });
      await reload();
      await onChange();
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    }
  }

  function save(e: FormEvent) {
    e.preventDefault();
    void run(async () => {
      const { crm_webhook_secret, transfer_numbers, ...rest } = form as Row;
      await patch(`/admin/clients/${clientId}`, {
        ...rest,
        plan_id: rest.plan_id || null, caller_id: rest.caller_id || null, voice_id: rest.voice_id || null,
        complaint_contact: rest.complaint_contact || null, crm_webhook_url: rest.crm_webhook_url || null,
        max_concurrent_calls: Number(rest.max_concurrent_calls),
        transfer_numbers: transfer_numbers.split(",").map((n: string) => n.trim()).filter(Boolean),
        ...(crm_webhook_secret ? { crm_webhook_secret } : {}),
      });
      return "Client saved.";
    });
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">{client.name}</h2>
          <p className="text-sm text-gray-600">{formatNumber(client.remaining_minutes)} minutes remaining</p>
        </div>
        <Button variant="secondary" onClick={() => onOpenClient({ id: client.id, name: client.name })}>Open client's portal</Button>
      </div>
      <Message error={feedback.error} success={feedback.success} />

      <Card title="Settings">
        <form onSubmit={save} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Client name"><Input value={form.name} onChange={set("name")} required /></Field>
          <Field label="Company name callers hear"><Input value={form.company_name} onChange={set("company_name")} required /></Field>
          <Field label="Status">
            <Select value={form.status} onChange={set("status")}><option value="active">Active</option><option value="suspended">Suspended</option></Select>
          </Field>
          <Field label="Plan">
            <Select value={form.plan_id} onChange={set("plan_id")}>
              <option value="">No plan</option>
              {plans?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          </Field>
          <Field label="Assigned phone number"><Input value={form.caller_id} onChange={set("caller_id")} /></Field>
          <Field label="Concurrent call limit"><Input type="number" min={0} value={form.max_concurrent_calls} onChange={set("max_concurrent_calls")} /></Field>
          <Field label="Agent name"><Input value={form.agent_persona_name} onChange={set("agent_persona_name")} /></Field>
          <Field label="Voice ID" hint="Empty uses the default voice"><Input value={form.voice_id} onChange={set("voice_id")} /></Field>
          <Field label="Complaint contact"><Input value={form.complaint_contact} onChange={set("complaint_contact")} /></Field>
          <Field label="Sales desk numbers" hint="For warm transfers, comma-separated"><Input value={form.transfer_numbers} onChange={set("transfer_numbers")} /></Field>
          {/* CRM webhook fields are hidden until CRM/Excel sync is offered (future plan); the backend still supports them */}
          <label className="flex items-center gap-2 text-sm text-gray-800 sm:col-span-2 lg:col-span-3">
            <input type="checkbox" checked={form.is_trial} onChange={(e) => setForm({ ...form, is_trial: e.target.checked })} />
            Free trial: browser test calls only. Untick once they buy a plan and their content is reviewed, to turn on phone calls.
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-800 sm:col-span-2 lg:col-span-3">
            <input type="checkbox" checked={form.payment_links_enabled} onChange={(e) => setForm({ ...form, payment_links_enabled: e.target.checked })} />
            Allow payment links. All links currently settle to the one Razorpay account in settings.
          </label>
          <div className="sm:col-span-2 lg:col-span-3"><Button type="submit">Save settings</Button></div>
        </form>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Add or remove minutes">
          <form className="grid gap-3 sm:grid-cols-2 items-end" onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const r = await post(`/admin/clients/${clientId}/minutes`, { minutes: Number(minutes), note: minutesNote });
              setMinutes(""); setMinutesNote("");
              return `Balance is now ${formatNumber(r.remaining_minutes)} minutes.`;
            });
          }}>
            <Field label="Minutes" hint="Negative removes minutes"><Input type="number" value={minutes} onChange={(e) => setMinutes(e.target.value)} required /></Field>
            <Field label="Reason"><Input value={minutesNote} onChange={(e) => setMinutesNote(e.target.value)} required minLength={2} /></Field>
            <Button type="submit" className="justify-self-start">Apply</Button>
          </form>
        </Card>
        <Card title="Client logins">
          <form className="flex flex-wrap items-end gap-3 mb-3" onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              const r = await post(`/admin/clients/${clientId}/users`, { email });
              setEmail("");
              return `Login created for ${r.email}. Temporary password, shown only now: ${r.temporary_password}`;
            });
          }}>
            <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></Field>
            <Button type="submit">Create login</Button>
          </form>
          <Table<Row> rows={client.users} rowKey={(u) => u.id} empty="No logins yet." columns={[
            { label: "Email", render: (u) => u.email },
            { label: "Last sign-in", render: (u) => formatTime(u.last_login_at) },
          ]} />
        </Card>
      </div>

      <ContentEditor clientId={clientId} content={client.content} run={run} />
    </>
  );
}

function ContentEditor({ clientId, content, run }: { clientId: string; content: Row; run: (fn: () => Promise<string>) => Promise<void> }) {
  const pretty = (v: unknown) => JSON.stringify(v, null, 2);
  const example = [{ sku: "GOLD", plan: "Monthly", name: "Gold Plan", price_inr: 999, billing_period: "monthly", description: "One line on what this is", features: ["Feature one"], eta_text: "10 minute mein activation" }];
  const [products, setProducts] = useState(pretty(content.products.length ? content.products : example));
  const [policies, setPolicies] = useState(pretty(content.policies));
  const { INTRODUCTION: intro = "", ...stagePrompts } = (content.stage_prompts ?? {}) as Row;
  const [about, setAbout] = useState<string>(intro);
  const [stages, setStages] = useState(pretty(stagePrompts));
  const [objections, setObjections] = useState(pretty(content.objection_playbooks));
  const [error, setError] = useState<string | null>(null);

  function save() {
    let body: Row;
    try {
      // The company introduction is stored as the INTRODUCTION script entry, which the agent reads
      const stagePromptsBody = { ...JSON.parse(stages), ...(about.trim() ? { INTRODUCTION: about.trim() } : {}) };
      body = { products: JSON.parse(products), policies: JSON.parse(policies), stage_prompts: stagePromptsBody, objection_playbooks: JSON.parse(objections) };
      setError(null);
    } catch {
      setError("One of the boxes is not valid JSON. Check for missing commas or quotes.");
      return;
    }
    void run(async () => {
      const r = await put(`/admin/clients/${clientId}/content`, body);
      return `Content saved. ${r.campaigns_updated} campaigns use the new script from their next call.`;
    });
  }

  return (
    <Card title="Products and script" action={<Button onClick={save}>Save content</Button>}>
      <p className="mb-3 text-xs text-gray-600">Saving approves this content for live calls. Prices come only from Products. Keep objection answers to about 18 words. Objection types: price, timing, trust, competitor, need. Policy topics: refunds, cancellation, data_residency.</p>
      <Message error={error} />
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="lg:col-span-2">
          <Field label="About the company" hint="2-3 short lines on what the company does, in plain text. The agent uses it to introduce the company; products are named from Products.">
            <Textarea value={about} onChange={setAbout} rows={4} />
          </Field>
        </div>
        <Field label="Products"><Textarea value={products} onChange={setProducts} rows={14} /></Field>
        <Field label="Objection answers"><Textarea value={objections} onChange={setObjections} rows={14} /></Field>
        <Field label="Policies"><Textarea value={policies} onChange={setPolicies} rows={10} /></Field>
        <Field label="Stage instructions"><Textarea value={stages} onChange={setStages} rows={10} /></Field>
      </div>
    </Card>
  );
}

// ── Plans ────────────────────────────────────────────────────
function Plans() {
  const { data, reload } = useData<Row[]>("/admin/plans");
  const blank = { id: "", name: "", included_minutes: "30000", rate_per_minute_inr: "2.5", billing_increment_seconds: "1" };
  const [form, setForm] = useState(blank);
  const [feedback, setFeedback] = useState<Feedback>({});
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body = { name: form.name, included_minutes: Number(form.included_minutes), rate_per_minute_inr: Number(form.rate_per_minute_inr),
      billing_increment_seconds: Number(form.billing_increment_seconds), is_active: true };
    try {
      if (form.id) await put(`/admin/plans/${form.id}`, body);
      else await post("/admin/plans", body);
      setFeedback({ success: "Plan saved. A changed billing rule applies to calls from now on." });
      setForm(blank);
      await reload();
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    }
  }

  return (
    <>
      <Message error={feedback.error} success={feedback.success} />
      <Card title={form.id ? "Edit plan" : "New plan"}>
        <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5 items-end">
          <Field label="Name"><Input value={form.name} onChange={set("name")} required /></Field>
          <Field label="Included minutes"><Input type="number" min={0} value={form.included_minutes} onChange={set("included_minutes")} /></Field>
          <Field label="₹ per minute"><Input type="number" min={0} step="0.01" value={form.rate_per_minute_inr} onChange={set("rate_per_minute_inr")} /></Field>
          <Field label="Billing rule">
            <Select value={form.billing_increment_seconds} onChange={set("billing_increment_seconds")}>
              <option value="1">Exact answered time</option>
              <option value="30">Round up to 30 seconds</option>
              <option value="60">Round up to a full minute</option>
            </Select>
          </Field>
          <Button type="submit" className="justify-self-start">Save plan</Button>
        </form>
      </Card>
      <Card title="Plans">
        <Table<Row> rows={data ?? []} rowKey={(p) => p.id} empty="No plans yet."
          onRowClick={(p) => setForm({ id: p.id, name: p.name, included_minutes: String(p.included_minutes), rate_per_minute_inr: String(p.rate_per_minute_inr), billing_increment_seconds: String(p.billing_increment_seconds) })}
          columns={[
            { label: "Name", render: (p) => p.name },
            { label: "Included minutes", align: "right", render: (p) => formatNumber(p.included_minutes) },
            { label: "₹ per minute", align: "right", render: (p) => p.rate_per_minute_inr },
            { label: "Billing rule", render: (p) => (p.billing_increment_seconds === 1 ? "Exact answered time" : `Round up to ${p.billing_increment_seconds} seconds`) },
          ]} />
      </Card>
    </>
  );
}

// ── Credit requests ──────────────────────────────────────────
function CreditRequests({ onChange }: { onChange: () => Promise<void> }) {
  const { data, reload } = useData<Row[]>("/admin/credit-requests?status=pending");
  const [feedback, setFeedback] = useState<Feedback>({});

  async function decide(id: number, decision: "approve" | "reject") {
    try {
      const r = await post(`/admin/credit-requests/${id}/${decision}`);
      setFeedback({ success: decision === "approve" ? `Approved. The client now has ${formatNumber(r.remaining_minutes)} minutes.` : "Request rejected." });
      await reload();
      await onChange();
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    }
  }

  return (
    <>
      <Message error={feedback.error} success={feedback.success} />
      <Card title="Waiting for a decision">
        <p className="mb-3 text-xs text-gray-600">Take payment outside the system first. Approving adds the minutes straight away and any waiting calls resume.</p>
        <Table<Row> rows={data ?? []} rowKey={(r) => r.id} empty="Nothing waiting." columns={[
          { label: "Client", render: (r) => r.client_name },
          { label: "Minutes", align: "right", render: (r) => formatNumber(r.minutes) },
          { label: "Note", render: (r) => r.note ?? "—" },
          { label: "Requested", render: (r) => formatTime(r.created_at) },
          { label: "", render: (r) => (
            <div className="flex justify-end gap-2">
              <Button onClick={() => decide(r.id, "approve")}>Approve</Button>
              <Button variant="secondary" onClick={() => decide(r.id, "reject")}>Reject</Button>
            </div>
          ) },
        ]} />
      </Card>
    </>
  );
}

// ── Knowledge approvals ──────────────────────────────────────
function KnowledgeReview({ onChange }: { onChange: () => Promise<void> }) {
  const { data, reload } = useData<Row[]>("/admin/knowledge?status=pending");
  const [preview, setPreview] = useState<{ doc: Row; chunks: Row[] } | null>(null);
  const [feedback, setFeedback] = useState<Feedback>({});

  async function decide(id: number, decision: "approve" | "reject") {
    try {
      await post(`/admin/knowledge/${id}/${decision}`);
      setFeedback({ success: decision === "approve" ? "Approved. The agent can use it from the next call." : "Rejected. It will not be used on calls." });
      setPreview(null);
      await reload();
      await onChange();
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    }
  }

  return (
    <>
      <Message error={feedback.error} success={feedback.success} />
      <Card title="Documents waiting for approval">
        <p className="mb-3 text-xs text-gray-600">Read a document before approving. The agent may speak anything in it, including prices and promises.</p>
        <Table<Row> rows={data ?? []} rowKey={(d) => d.id} empty="Nothing waiting." columns={[
          { label: "Client", render: (d) => d.client_name },
          { label: "File", render: (d) => d.filename },
          { label: "Sections", align: "right", render: (d) => d.chunks },
          { label: "Uploaded", render: (d) => formatTime(d.created_at) },
          { label: "", render: (d) => (
            <div className="flex justify-end">
              <Button variant="secondary" onClick={async () => setPreview({ doc: d, chunks: await get<Row[]>(`/admin/knowledge/${d.id}/chunks`) })}>Review</Button>
            </div>
          ) },
        ]} />
      </Card>
      {preview && (
        <Card title={preview.doc.filename} action={
          <div className="flex gap-2">
            <Button onClick={() => decide(preview.doc.id, "approve")}>Approve</Button>
            <Button variant="danger" onClick={() => decide(preview.doc.id, "reject")}>Reject</Button>
          </div>
        }>
          <ol className="space-y-2 max-h-[32rem] overflow-y-auto">
            {preview.chunks.map((c, i) => (
              <li key={i} className="rounded-lg border border-white/70 p-2 text-sm">
                {c.question !== c.answer.slice(0, 120) && <p className="text-gray-600 mb-1">{c.question}</p>}
                <p className="text-gray-800 whitespace-pre-wrap">{c.answer}</p>
              </li>
            ))}
          </ol>
        </Card>
      )}
    </>
  );
}

// ── Audit log ────────────────────────────────────────────────
// ── Enquiries ────────────────────────────────────────────────
const ENQUIRY_STATUSES = ["new", "contacted", "converted"];

function Enquiries({ onChange }: { onChange: () => Promise<void> }) {
  const [status, setStatus] = useState("");
  const { data, error, reload } = useData<Row[]>(`/admin/enquiries${status ? `?status=${status}` : ""}`);
  const [feedback, setFeedback] = useState<Feedback>({});
  const whatsapp = (n: string) => `https://wa.me/${n.replace(/\D/g, "")}`;

  async function change(id: number, next: string) {
    try {
      await patch(`/admin/enquiries/${id}`, { status: next });
      setFeedback({});
      await reload();
      await onChange();
    } catch (err) {
      setFeedback({ error: (err as Error).message });
    }
  }

  return (
    <Card title="Enquiries" action={
      <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Show">
        <option value="">All</option>
        {ENQUIRY_STATUSES.map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
      </Select>
    }>
      <Message error={error || feedback.error} />
      <Table<Row>
        rows={data ?? []}
        rowKey={(r) => r.id}
        empty="No enquiries yet."
        columns={[
          { label: "When", render: (r) => <span className="whitespace-nowrap">{formatTime(r.created_at)}</span> },
          { label: "Type", render: (r) => ({ pricing: "Pricing (client)", signup: "Trial sign-up" } as Row)[r.topic] ?? "Services" },
          { label: "Person", render: (r) => r.person_name },
          { label: "Contact", render: (r) => (
            <div className="text-xs tabular-nums">
              {r.phone_number && <p>{r.phone_number}</p>}
              {r.whatsapp_number && <a className="text-violet-700 underline" href={whatsapp(r.whatsapp_number)} target="_blank" rel="noreferrer">WhatsApp {r.whatsapp_number}</a>}
            </div>
          ) },
          { label: "Business", render: (r) => [r.business_name, r.business_type].filter(Boolean).join(" · ") || "—" },
          { label: "Note", render: (r) => <span className="text-xs text-gray-600">{r.note ?? "—"}</span> },
          { label: "Status", render: (r) => (
            <Select value={r.status} onChange={(e) => change(r.id, e.target.value)} aria-label="Status">
              {ENQUIRY_STATUSES.map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
            </Select>
          ) },
        ]}
      />
    </Card>
  );
}

function Audit() {
  const { data } = useData<Row[]>("/admin/audit?limit=200");
  return (
    <Card title="Recent actions">
      <Table<Row> rows={data ?? []} rowKey={(a) => a.id} empty="Nothing recorded yet." columns={[
        { label: "When", render: (a) => formatTime(a.at) },
        { label: "Who", render: (a) => a.actor },
        { label: "Action", render: (a) => a.action.replaceAll("_", " ") },
        { label: "Client", render: (a) => a.client_id ?? "—" },
        { label: "Details", render: (a) => (a.details ? <code className="text-xs text-gray-600">{JSON.stringify(a.details)}</code> : "—") },
      ]} />
    </Card>
  );
}
