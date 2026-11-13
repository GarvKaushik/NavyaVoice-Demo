/* ─────────────────────────────────────────────────────────────
 * Landing page "Enquire about services": the onboarding steps,
 * animated one after another, beside a short enquiry form.
 * ─────────────────────────────────────────────────────────── */
import { useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { post } from "./api";
import { Field, Input, Message, Select } from "./ui";

const JOURNEY = [
  { title: "You fill the form", text: "Tell us a little about your business. It takes a minute." },
  { title: "We contact you", text: "We reach you on WhatsApp to understand what you sell and who you call." },
  { title: "You choose a plan", text: "Pick the minutes that fit your calling volume." },
  { title: "You get your login", text: "We set up your agent and send your login. Test it, then start calling." },
];

const BUSINESS_TYPES = ["Retail", "Real estate", "Education", "Healthcare", "Finance", "Services", "Other"];

/** The four onboarding steps; each lights up in turn, and the line between them fills as it goes. */
export function Journey() {
  return (
    <ol className="journey relative space-y-6" aria-label="How onboarding works">
      <span className="journey-track absolute left-[1.15rem] top-3 bottom-3 w-0.5 rounded-full bg-black/10" aria-hidden="true">
        <span className="journey-fill block h-full w-full origin-top rounded-full bg-lime-400" />
      </span>
      {JOURNEY.map((step, i) => (
        <li key={step.title} className="journey-step relative flex gap-4" style={{ "--i": i } as CSSProperties}>
          <span className="journey-dot relative z-10 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white/50 text-sm font-medium text-gray-900 shadow-sm ring-1 ring-white/80 backdrop-blur-md">
            {i + 1}
          </span>
          <div className="pt-1">
            <h3 className="font-medium text-gray-900">{step.title}</h3>
            <p className="mt-0.5 text-sm text-gray-600">{step.text}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function EnquiryForm() {
  const [form, setForm] = useState({ person_name: "", phone_number: "", whatsapp_number: "", business_name: "",
    business_type: "", website: "" });
  const [sameNumber, setSameNumber] = useState(true);
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSending(true);
    setError(null);
    try {
      await post("/enquiries", { ...form, whatsapp_number: sameNumber ? form.phone_number : form.whatsapp_number,
        business_name: form.business_name || null, business_type: form.business_type || null });
      setDone(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  }

  if (done) {
    return (
      <div className="pop flex flex-col items-center justify-center py-10 text-center" role="status">
        <span className="flex h-14 w-14 items-center justify-center rounded-full bg-lime-300 text-2xl text-neutral-900" aria-hidden="true">✓</span>
        <h3 className="mt-4 text-xl font-medium tracking-tight text-gray-900">Thank you, {form.person_name.split(" ")[0]}!</h3>
        <p className="mt-1 max-w-xs text-sm text-gray-600">We'll contact you on WhatsApp within one working day.</p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <div className="sm:col-span-2"><Message error={error} /></div>
      <Field label="Your name"><Input value={form.person_name} onChange={set("person_name")} required minLength={2} autoComplete="name" /></Field>
      <Field label="Phone number"><Input value={form.phone_number} onChange={set("phone_number")} required type="tel" autoComplete="tel" placeholder="98765 43210" /></Field>
      <label className="flex items-center gap-2 text-sm text-gray-700 sm:col-span-2">
        <input type="checkbox" checked={sameNumber} onChange={(e) => setSameNumber(e.target.checked)} />
        My WhatsApp number is the same
      </label>
      {!sameNumber && (
        <div className="sm:col-span-2">
          <Field label="WhatsApp number"><Input value={form.whatsapp_number} onChange={set("whatsapp_number")} required type="tel" placeholder="98765 43210" /></Field>
        </div>
      )}
      <Field label="Business name (optional)"><Input value={form.business_name} onChange={set("business_name")} autoComplete="organization" /></Field>
      <Field label="Business type (optional)">
        <Select value={form.business_type} onChange={set("business_type")}>
          <option value="">Choose one</option>
          {BUSINESS_TYPES.map((t) => <option key={t}>{t}</option>)}
        </Select>
      </Field>
      {/* Hidden from people; bots that fill every field give themselves away */}
      <input type="text" name="website" value={form.website} onChange={set("website")} tabIndex={-1} autoComplete="off"
        aria-hidden="true" className="absolute -left-[9999px] h-px w-px opacity-0" />
      <button type="submit" disabled={sending}
        className="btn-bounce sm:col-span-2 mt-1 rounded-full bg-neutral-900 px-6 py-3 font-medium text-white transition-colors hover:bg-neutral-700 disabled:opacity-60">
        {sending ? "Sending…" : "Send enquiry"}
      </button>
      <p className="text-xs text-gray-500 sm:col-span-2">We use your number only to contact you about this enquiry.</p>
    </form>
  );
}
