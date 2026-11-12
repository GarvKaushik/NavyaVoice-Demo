/* ─────────────────────────────────────────────────────────────
 * Public landing page. Sections scroll in place because the hash
 * in the URL is used for page routing.
 * ─────────────────────────────────────────────────────────── */
import { useId } from "react";
import type { CSSProperties, ReactNode } from "react";
import DemoCall from "./DemoCall";
import { Icon } from "./icons";
import type { IconName } from "./icons";

/** Liquid glass voice orb: an "n" whose voice trails off in two bars, navya (new) + vyakta (voiced). */
export function LogoMark({ className = "h-7 w-7" }: { className?: string }) {
  const id = `logo${useId().replace(/\W/g, "")}`; // gradient ids must be unique when several logos are on one page
  return (
    <svg viewBox="0 0 64 64" className={`shrink-0 ${className}`} aria-hidden="true">
      <defs>
        <radialGradient id={`${id}orb`} cx=".35" cy=".3" r=".8">
          <stop offset="0" stopColor="#F3FCC8" />
          <stop offset=".45" stopColor="#D3F062" />
          <stop offset="1" stopColor="#9C86EE" />
        </radialGradient>
        <linearGradient id={`${id}shine`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity=".85" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <circle cx="32" cy="32" r="29" fill={`url(#${id}orb)`} />
      <circle cx="32" cy="32" r="28.25" fill="none" stroke="#fff" strokeOpacity=".7" strokeWidth="1.5" />
      <ellipse cx="25" cy="15" rx="15" ry="7" fill={`url(#${id}shine)`} />
      <g fill="none" stroke="#141414" strokeWidth="5" strokeLinecap="round">
        <path d="M17 44V32a8.5 8.5 0 0 1 17 0v12" />
        <path d="M41.5 28v16M49 33v6" />
      </g>
    </svg>
  );
}

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <LogoMark />
      {compact ? <span className="sr-only">Navyakta AI</span> : (
        <span className="inline-flex items-center gap-1.5 text-lg font-semibold tracking-[-0.03em]">
          navyakta
          <span className="rounded-full bg-lime-300 px-1.5 py-px text-[10px] font-medium tracking-[0.08em] text-neutral-900">AI</span>
        </span>
      )}
    </span>
  );
}

const delay = (ms: number) => ({ "--d": `${ms}ms` }) as CSSProperties;

const FACTS = [
  { value: "Hinglish", label: "Hindi and English, the way customers talk" },
  { value: "Per minute", label: "You pay only for answered minutes" },
  { value: "Every call", label: "Opens by saying it is an AI assistant" },
  { value: "DND + opt-outs", label: "Checked before every dial" },
];

const STEPS = [
  { title: "Add your product and leads", text: "Share your plans, prices and FAQs, then upload leads with their consent source." },
  { title: "Test it yourself", text: "Talk to your agent from the browser, or have it ring your own phone, before any customer hears it." },
  { title: "Start a campaign", text: "Calls go out within calling hours. Busy and unanswered numbers are retried, and callbacks happen when asked for." },
  { title: "Follow up on results", text: "Every call ends with a clear result, so your team knows exactly who to call next." },
];

const CALL_FLOW = [
  { title: "Confirms the person", text: "Introduces itself as an AI assistant and checks it is speaking to the right person." },
  { title: "Asks for two minutes", text: "A short, polite ask. A clear no ends the call straight away." },
  { title: "Explains in one line", text: "What your product does for this customer, in plain Hinglish." },
  { title: "Answers questions", text: "Price, features and offers come only from your approved details, never made up." },
  { title: "Handles objections", text: "Too expensive, no time, already using something: each has a calm, honest answer." },
  { title: "Closes the next step", text: "Books a demo, schedules a callback or sends a payment link on WhatsApp." },
];

const RESULTS = [
  { label: "Ready", style: "bg-lime-300 text-neutral-900", text: "Booked a demo, asked for a payment link or agreed to buy." },
  { label: "Contact again", style: "bg-violet-200 text-violet-700", text: "Asked for a callback or wanted more time." },
  { label: "Talk to manager", style: "bg-sky-100 text-sky-800", text: "Wants a person from your team to call." },
  { label: "Refused", style: "bg-red-100 text-red-800", text: "Not interested or asked not to be called. Blocked for your account." },
  { label: "Unclear", style: "bg-black/5 text-gray-700", text: "The call ended before a clear answer." },
];

const FEATURES: { icon: IconName; title: string; text: string }[] = [
  { icon: "mic", title: "Natural Hinglish voice", text: "Short, warm replies with Indian pronunciation of prices, dates and times." },
  { icon: "fileCheck", title: "Approved answers only", text: "Every price, date and promise is checked against your details before it is spoken." },
  { icon: "book", title: "Your own knowledge", text: "Upload documents so the agent answers more questions, once an admin approves them." },
  { icon: "list", title: "Transcript and summary", text: "Read the whole conversation and a short summary with the next step for every call." },
  { icon: "phone", title: "Retries and callbacks", text: "Busy and unanswered numbers are tried again, and requested callbacks are scheduled." },
  { icon: "link", title: "Connects to your CRM", text: "Call results are sent to your CRM by webhook as soon as a call ends." },
  { icon: "wallet", title: "Minutes at a glance", text: "See minutes used and left, and request more in one click. Calls never cut mid-way." },
  { icon: "users", title: "Your own agent", text: "Your company name, agent persona, voice and caller ID on every call." },
];

const TRUST = [
  { title: "Honest about being AI", text: "Every call opens by saying the customer is talking to an AI assistant." },
  { title: "Opt-outs are instant", text: "Anyone who asks not to be called is blocked for your account straight away." },
  { title: "DND and calling hours", text: "National DND entries and permitted calling hours are checked before each dial." },
  { title: "Your data stays yours", text: "Leads, calls and documents are kept separate for every company. Transcript is off unless you turn it on." },
];

const EXAMPLE = [
  { agent: true, text: "Namaste! Main Priya bol rahi hoon, ek AI assistant. Kya meri baat Rajesh ji se ho rahi hai?" },
  { agent: false, text: "Haan boliye, price kya hai?" },
  { agent: true, text: "Rajesh ji, yeh plan ₹1,499 per month hai. Kya main payment link WhatsApp par bhej doon?" },
  { agent: false, text: "Theek hai, bhej dijiye." },
];

const NAV_LINKS = [
  { id: "how", label: "How it works" },
  { id: "call", label: "On a call" },
  { id: "results", label: "Results" },
  { id: "trust", label: "Trust" },
];

function Section({ id, eyebrow, title, intro, children }: { id: string; eyebrow: string; title: string; intro?: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 mx-auto max-w-6xl px-4 py-16 sm:py-24">
      <div className="reveal">
        <p className="font-mono text-xs uppercase tracking-[0.16em] text-violet-700">{eyebrow}</p>
        <h2 className="mt-3 max-w-2xl text-3xl font-medium tracking-[-0.02em] text-gray-900 sm:text-4xl">{title}</h2>
        {intro && <p className="mt-3 max-w-2xl text-gray-600">{intro}</p>}
      </div>
      <div className="mt-12">{children}</div>
    </section>
  );
}

export default function Landing({ onLogin }: { onLogin: () => void }) {
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });

  return (
    <div className="min-h-screen text-gray-900">
      {/* Floating dark navigation bar */}
      <header className="sticky top-4 z-20 px-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 rounded-full bg-neutral-900 py-2.5 pl-6 pr-2.5 text-white shadow-2xl shadow-black/20">
          <Logo />
          <nav className="flex items-center gap-1">
            {NAV_LINKS.map((link) => (
              <button key={link.id} onClick={() => scrollTo(link.id)}
                className="hidden rounded-full px-3 py-1.5 text-sm text-neutral-300 transition-colors hover:bg-white/10 hover:text-white md:block">
                {link.label}
              </button>
            ))}
            <button onClick={onLogin} className="ml-1 rounded-full bg-lime-300 px-5 py-2 text-sm font-medium text-neutral-900 transition-colors hover:bg-lime-400">Login</button>
          </nav>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 pt-16 pb-12 sm:pt-24 lg:grid-cols-[1.1fr_1fr]">
          <div>
            <p className="rise inline-flex items-center gap-2 rounded-full glass px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-gray-700">
              <span className="h-1.5 w-1.5 rounded-full bg-lime-500" aria-hidden="true" />
              AI voice agent for Indian sales teams
            </p>
            <h1 className="rise mt-5 text-4xl font-medium leading-[1.05] tracking-[-0.035em] text-gray-900 sm:text-6xl" style={delay(80)}>
              Sales calls in <span className="bg-lime-300 px-2">Hinglish</span> that sound like a person, not a script
            </h1>
            <p className="rise mt-6 max-w-xl text-lg text-gray-600" style={delay(160)}>
              Navyakta AI calls your leads, answers their questions from your approved details, books demos and
              callbacks, and tells you exactly who is ready to buy.
            </p>
            <div className="rise mt-8 flex flex-wrap gap-3" style={delay(240)}>
              <button onClick={onLogin} className="rounded-full bg-neutral-900 px-6 py-3 font-medium text-white transition-colors hover:bg-neutral-700">Login</button>
              <button onClick={() => scrollTo("how")} className="glass rounded-full px-6 py-3 font-medium text-gray-900">See how it works</button>
            </div>
          </div>

          <div className="rise" style={delay(300)}>
            <DemoCall fallback={
              <figure className="glass rounded-3xl p-5">
                <figcaption className="mb-4 flex items-center justify-between text-xs text-gray-600">
                  <span className="font-mono uppercase tracking-[0.12em]">Example call</span>
                  <span className="rounded-full bg-lime-300 px-2.5 py-0.5 font-medium text-neutral-900">Result: Ready</span>
                </figcaption>
                <ul className="space-y-3">
                  {EXAMPLE.map((line, i) => (
                    <li key={line.text} className={`rise flex ${line.agent ? "" : "justify-end"}`} style={delay(500 + i * 350)}>
                      <p className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm ${line.agent ? "rounded-bl-sm bg-white text-gray-900" : "rounded-br-sm bg-neutral-900 text-white"}`}>
                        {line.text}
                      </p>
                    </li>
                  ))}
                </ul>
              </figure>
            } />
          </div>
        </section>

        <div className="mx-auto max-w-6xl px-4">
          <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {FACTS.map((fact, i) => (
              <div key={fact.value} className="rise glass rounded-2xl p-4" style={delay(400 + i * 80)}>
                <dt className="text-lg font-medium tracking-tight text-gray-900">{fact.value}</dt>
                <dd className="mt-1 text-sm text-gray-600">{fact.label}</dd>
              </div>
            ))}
          </dl>
        </div>

        <Section id="how" eyebrow="How it works" title="From a lead list to people ready to buy">
          <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, i) => (
              <li key={step.title} className="reveal glass rounded-3xl p-5">
                <span className="font-mono text-xs text-gray-500">0{i + 1}</span>
                <h3 className="mt-3 text-lg font-medium tracking-tight text-gray-900">{step.title}</h3>
                <p className="mt-2 text-sm text-gray-600">{step.text}</p>
              </li>
            ))}
          </ol>
        </Section>

        <Section id="call" eyebrow="On a call" title="A short, polite conversation with one goal"
          intro="The agent follows the same steps on every call and keeps replies short, so customers stay on the line.">
          <ol className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {CALL_FLOW.map((stage, i) => (
              <li key={stage.title} className="reveal flex gap-4 rounded-3xl bg-neutral-900 p-5 text-white">
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-medium ${
                  i === CALL_FLOW.length - 1 ? "bg-lime-300 text-neutral-900" : "bg-white/10 text-white"}`}>{i + 1}</span>
                <div>
                  <h3 className="font-medium">{stage.title}</h3>
                  <p className="mt-1 text-sm text-neutral-400">{stage.text}</p>
                </div>
              </li>
            ))}
          </ol>
        </Section>

        <Section id="results" eyebrow="Results" title="Every call ends with a clear answer"
          intro="Each call gets one of five results, with the transcript and a short summary of what to do next.">
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {RESULTS.map((result) => (
              <li key={result.label} className="reveal glass rounded-3xl p-5">
                <span className={`inline-block rounded-full px-3 py-1 text-sm font-medium ${result.style}`}>{result.label}</span>
                <p className="mt-3 text-sm text-gray-600">{result.text}</p>
              </li>
            ))}
          </ul>
        </Section>

        <Section id="features" eyebrow="Features" title="Everything you need to run calling campaigns">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="reveal glass rounded-3xl p-5">
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-violet-200 text-violet-700">
                  <Icon name={feature.icon} className="h-5 w-5" />
                </span>
                <h3 className="mt-4 font-medium text-gray-900">{feature.title}</h3>
                <p className="mt-1.5 text-sm text-gray-600">{feature.text}</p>
              </div>
            ))}
          </div>
        </Section>

        <Section id="trust" eyebrow="Trust" title="Built to respect the people you call">
          <ul className="grid gap-4 sm:grid-cols-2">
            {TRUST.map((item) => (
              <li key={item.title} className="reveal flex gap-4 glass rounded-3xl p-5">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-lime-300 text-neutral-900">
                  <Icon name="shield" className="h-5 w-5" />
                </span>
                <div>
                  <h3 className="font-medium text-gray-900">{item.title}</h3>
                  <p className="mt-1 text-sm text-gray-600">{item.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </Section>

        <section className="mx-auto max-w-6xl px-4 pb-20">
          <div className="reveal flex flex-wrap items-center justify-between gap-6 rounded-[2rem] bg-neutral-900 p-8 text-white sm:p-12">
            <div className="max-w-xl">
              <h2 className="text-3xl font-medium tracking-[-0.02em]">Pay only for answered minutes</h2>
              <p className="mt-2 text-neutral-400">
                Ringing and unanswered calls cost nothing. Log in to see your calls, results and minutes left.
              </p>
            </div>
            <button onClick={onLogin} className="rounded-full bg-lime-300 px-6 py-3 font-medium text-neutral-900 transition-colors hover:bg-lime-400">Login</button>
          </div>
        </section>
      </main>

      <footer className="border-t border-black/5">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-sm text-gray-500">
          <Logo />
          <span>Hinglish AI sales calls by NavyaNexus AI</span>
        </div>
      </footer>
    </div>
  );
}
