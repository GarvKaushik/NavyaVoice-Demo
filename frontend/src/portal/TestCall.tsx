/* ─────────────────────────────────────────────────────────────
 * Test & call: talk to the agent in the browser (no phone leg,
 * free test minutes), or have it call a real phone number now
 * with the campaign's script (uses call minutes).
 * ─────────────────────────────────────────────────────────── */
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { formatDuration, get, parseTime, post, scoped } from "./api";
import type { Row } from "./api";
import { useAgentCall } from "./agentCall";
import type { CallTicket } from "./agentCall";
import { Button, Card, Empty, Field, Input, Message, Select, useData } from "./ui";

type Mode = "browser" | "phone";

export default function TestCall({ clientId, used, limit }: { clientId?: string; used: number; limit: number }) {
  const { data: campaigns } = useData<Row[]>(scoped("/client/campaigns", clientId));
  const [mode, setMode] = useState<Mode>("browser");
  const [campaignId, setCampaignId] = useState("");
  const selected = campaignId || campaigns?.[0]?.id || "";

  if (campaigns && !campaigns.length) {
    return <Card title="Test & call"><Empty>Create a campaign first, then you can test it here.</Empty></Card>;
  }

  return (
    <Card title="Test & call">
      <div role="tablist" aria-label="How to call" className="mb-4 inline-grid grid-cols-2 gap-1 rounded-full bg-white/70 p-1">
        {([["browser", "In this browser"], ["phone", "On a phone"]] as const).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={mode === key} onClick={() => setMode(key)}
            className={`rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
              mode === key ? "bg-neutral-900 text-white" : "text-gray-600 hover:text-gray-900"}`}>
            {label}
          </button>
        ))}
      </div>

      <div className="max-w-2xl">
        <Field label="Campaign">
          <Select value={selected} onChange={(e) => setCampaignId(e.target.value)}>
            {campaigns?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        </Field>
      </div>

      {mode === "browser" ? (
        <BrowserCall clientId={clientId} campaignId={selected} used={used} limit={limit} />
      ) : (
        <PhoneCall clientId={clientId} campaignId={selected} />
      )}
    </Card>
  );
}

/** The current time, updated every second while `active`. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

const seconds = (from: number, to: number) => Math.max(0, Math.floor((to - from) / 1000));

function BrowserCall({ clientId, campaignId, used, limit }: { clientId?: string; campaignId: string; used: number; limit: number }) {
  const [name, setName] = useState("Rajesh");
  const call = useAgentCall();
  const liveSince = useRef<number | null>(null);
  const [lasted, setLasted] = useState<number | null>(null);
  const now = useNow(call.state === "live");

  useEffect(() => {
    if (call.state === "live") {
      liveSince.current = Date.now();
      setLasted(null);
    } else if (call.state === "idle" && liveSince.current !== null) {
      setLasted(seconds(liveSince.current, Date.now()));
      liveSince.current = null;
    }
  }, [call.state]);

  const start = () =>
    call.start(() => post<CallTicket>(scoped("/client/test-call", clientId), { campaign_id: campaignId, customer_name: name }));

  return (
    <div className="mt-3 grid max-w-2xl gap-3">
      <p className="text-sm text-gray-600">
        Talk to your agent from this browser before calling real leads. It uses your real script and voice, but does not
        use your call minutes. {used} of {limit} free test minutes used this month.
      </p>
      <Field label="Name the agent should call you">
        <Input value={name} onChange={(e) => setName(e.target.value)} disabled={call.state !== "idle"} />
      </Field>
      <div className="flex items-center gap-3">
        {call.state === "live" ? (
          <>
            <Button variant="danger" onClick={call.end}>End call</Button>
            <span className="text-sm text-green-700">
              ● Ongoing · {formatDuration(seconds(liveSince.current ?? now, now))} ·{" "}
              {call.agentSpeaking ? "The agent is speaking" : "Speak into your microphone."}
            </span>
          </>
        ) : (
          <>
            <Button onClick={start} disabled={call.state === "connecting" || !campaignId}>
              {call.state === "connecting" ? "Connecting…" : "Start test call"}
            </Button>
            {call.state === "idle" && lasted !== null && (
              <span className="text-sm text-gray-600">Call over · lasted {formatDuration(lasted)}</span>
            )}
          </>
        )}
      </div>
      <Message error={call.error} />
    </div>
  );
}

function PhoneCall({ clientId, campaignId }: { clientId?: string; campaignId: string }) {
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [callId, setCallId] = useState<string | null>(null);
  const [calling, setCalling] = useState<string | null>(null);
  const [status, setStatus] = useState<Row | null>(null);
  const ended = status?.status === "ended";
  const now = useNow(Boolean(callId) && !ended);

  // Follow the call while it rings and runs: our own API, every 2 seconds, until it ends
  useEffect(() => {
    if (!callId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const row = await get(scoped(`/client/calls/${callId}`, clientId));
        if (stopped) return;
        setStatus(row);
        if (row.status === "ended") return;
      } catch {
        /* the call record may not exist for a moment; keep trying */
      }
      if (!stopped) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [callId, clientId]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setCallId(null);
    setStatus(null);
    try {
      const result = await post(scoped("/client/phone-call", clientId),
        { campaign_id: campaignId, phone_number: phone, customer_name: name });
      setCalling(result.phone_number);
      setCallId(result.call_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-3 grid max-w-2xl gap-3 sm:grid-cols-2">
      <p className="text-sm text-gray-600 sm:col-span-2">
        The agent calls this number right away with the campaign's script. Answered time uses your call minutes. The number
        is saved as a lead of this campaign.
      </p>
      <Field label="Phone number" hint="With country code, e.g. +91 98765 43210">
        <Input type="tel" inputMode="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)}
          placeholder="+91 98765 43210" required />
      </Field>
      <Field label="Customer name (optional)">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Rajesh" />
      </Field>
      <div className="sm:col-span-2">
        <Button type="submit" disabled={busy || !campaignId || !phone.trim()}>{busy ? "Starting the call…" : "Call now"}</Button>
      </div>
      {callId && (
        <div className="sm:col-span-2" role="status" aria-live="polite">
          <CallStatus status={status} now={now} phone={calling} />
        </div>
      )}
      <div className="sm:col-span-2"><Message error={error} /></div>
    </form>
  );
}

function CallStatus({ status, now, phone }: { status: Row | null; now: number; phone: string | null }) {
  const box = "rounded-lg border px-3 py-2 text-sm";
  if (status?.status === "ended") {
    const result = status.answered
      ? `lasted ${formatDuration(status.answered_seconds)}`
      : (status.conclusion_label || "not answered").toLowerCase();
    return (
      <p className={`${box} border-gray-300 bg-white text-gray-700`}>
        <b className="text-gray-900">Call over</b> · {result}. The transcript and summary appear in Call results.
      </p>
    );
  }
  if (status?.answered && status.answered_at) {
    return (
      <p className={`${box} border-green-200 bg-green-100/30 text-green-800`}>
        ● <b>Ongoing</b> · {formatDuration(seconds(parseTime(status.answered_at).getTime(), now))}
      </p>
    );
  }
  return (
    <p className={`${box} border-amber-200 bg-amber-100/20 text-amber-800`}>
      <span className="animate-pulse">●</span> <b>Ringing</b> {phone}…
    </p>
  );
}
