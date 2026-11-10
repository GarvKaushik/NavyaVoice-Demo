/* ─────────────────────────────────────────────────────────────
 * Test & call: talk to the agent in the browser (no phone leg,
 * free test minutes), or have it call a real phone number now
 * with the campaign's script (uses call minutes).
 * ─────────────────────────────────────────────────────────── */
import { useState } from "react";
import type { FormEvent } from "react";
import { post, scoped } from "./api";
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
      <div role="tablist" aria-label="How to call" className="mb-4 inline-grid grid-cols-2 gap-1 rounded-lg bg-gray-950 p-1">
        {([["browser", "In this browser"], ["phone", "On a phone"]] as const).map(([key, label]) => (
          <button key={key} role="tab" aria-selected={mode === key} onClick={() => setMode(key)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === key ? "bg-indigo-600 text-white" : "text-gray-400 hover:text-white"}`}>
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

function BrowserCall({ clientId, campaignId, used, limit }: { clientId?: string; campaignId: string; used: number; limit: number }) {
  const [name, setName] = useState("Rajesh");
  const call = useAgentCall();

  const start = () =>
    call.start(() => post<CallTicket>(scoped("/client/test-call", clientId), { campaign_id: campaignId, customer_name: name }));

  return (
    <div className="mt-3 grid max-w-2xl gap-3">
      <p className="text-sm text-gray-400">
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
            <span className="text-sm text-green-300">● {call.agentSpeaking ? "The agent is speaking" : "On call. Speak into your microphone."}</span>
          </>
        ) : (
          <Button onClick={start} disabled={call.state === "connecting" || !campaignId}>
            {call.state === "connecting" ? "Connecting…" : "Start test call"}
          </Button>
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
  const [success, setSuccess] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await post(scoped("/client/phone-call", clientId),
        { campaign_id: campaignId, phone_number: phone, customer_name: name });
      setSuccess(`Calling ${result.phone_number} now. The result, transcript and summary appear in Call results when the call ends.`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-3 grid max-w-2xl gap-3 sm:grid-cols-2">
      <p className="text-sm text-gray-400 sm:col-span-2">
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
      <div className="sm:col-span-2"><Message error={error} success={success} /></div>
    </form>
  );
}
