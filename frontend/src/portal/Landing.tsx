/* ─────────────────────────────────────────────────────────────
 * Public landing page. Sections scroll in place because the hash
 * in the URL is used for page routing.
 * ─────────────────────────────────────────────────────────── */
import { useEffect, useId, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import DemoCall from "./DemoCall";
import { EnquiryForm, Journey } from "./Enquire";
import { Icon } from "./icons";
import type { IconName } from "./icons";

/** Liquid glass voice orb: an "n" whose voice trails off in two bars. */
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
      {compact ? <span className="sr-only">Navix AI</span> : (
        <span className="inline-flex items-center gap-1.5 text-lg font-semibold tracking-[-0.03em]">
          navix
          <span className="rounded-full bg-lime-300 px-1.5 py-px text-[10px] font-medium tracking-[0.08em] text-neutral-900">AI</span>
        </span>
      )}
    </span>
  );
}

const delay = (ms: number) => ({ "--d": `${ms}ms` }) as CSSProperties;

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Types each word, holds it, erases it, then types the next. Visitors who reduce motion see the first word only. */
function useTypewriter(words: string[], typeMs = 95, eraseMs = 50, holdMs = 1700): string {
  const [text, setText] = useState(words[0]);
  useEffect(() => {
    if (reducedMotion()) return;
    let word = 0, length = words[0].length, erasing = true, timer = 0;
    const tick = () => {
      const target = words[word];
      if (erasing) {
        length -= 1;
        if (length === 0) { erasing = false; word = (word + 1) % words.length; }
      } else if (length < words[word].length) {
        length += 1;
      }
      setText((erasing ? target : words[word]).slice(0, length));
      const done = !erasing && length === words[word].length;
      if (done) erasing = true;
      timer = window.setTimeout(tick, done ? holdMs : erasing ? eraseMs : typeMs);
    };
    timer = window.setTimeout(tick, holdMs);
    return () => window.clearTimeout(timer);
  }, [words, typeMs, eraseMs, holdMs]);
  return text;
}

/** Keeps --mx/--my on the hovered .liquid card at the pointer, so its blob can follow it. */
function useLiquidPointer() {
  useEffect(() => {
    const move = (e: PointerEvent) => {
      const card = (e.target as Element | null)?.closest?.(".liquid") as HTMLElement | null;
      if (!card) return;
      const box = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${e.clientX - box.left}px`);
      card.style.setProperty("--my", `${e.clientY - box.top}px`);
    };
    document.addEventListener("pointermove", move, { passive: true });
    return () => document.removeEventListener("pointermove", move);
  }, []);
}

/** The water-like blob inside a .liquid card. */
const Blob = () => <span className="liquid-blob" aria-hidden="true" />;

const HERO_WORDS = ["Hinglish", "Hindi", "English"];

// Drifting colour behind the sections, so the liquid glass blocks have something to bend
// Placed under the card rows (hero, facts, each section), so every glass block has colour to bend
const BACKDROP: CSSProperties[] = [
  { top: "2%", right: "2%", background: "rgb(205 234 85 / 0.8)" },
  { top: "9%", left: "-4rem", background: "rgb(188 174 247 / 0.75)", animationDelay: "-6s" },
  { top: "15%", left: "30%", background: "rgb(125 211 252 / 0.55)", animationDelay: "-11s" },
  { top: "24%", right: "8%", background: "rgb(188 174 247 / 0.7)", animationDelay: "-3s" },
  { top: "31%", left: "4%", background: "rgb(205 234 85 / 0.7)", animationDelay: "-15s" },
  { top: "45%", left: "35%", background: "rgb(125 211 252 / 0.5)", animationDelay: "-9s" },
  { top: "52%", right: "-4rem", background: "rgb(205 234 85 / 0.7)", animationDelay: "-18s" },
  { top: "60%", left: "-2rem", background: "rgb(188 174 247 / 0.75)", animationDelay: "-5s" },
  { top: "70%", left: "40%", background: "rgb(205 234 85 / 0.6)", animationDelay: "-13s" },
  { top: "79%", right: "6%", background: "rgb(125 211 252 / 0.5)", animationDelay: "-20s" },
  { top: "88%", left: "8%", background: "rgb(188 174 247 / 0.7)", animationDelay: "-7s" },
];

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
  { title: "Closes the next step", text: "Books a demo, sets up a meeting with your team, or schedules a callback." },
];

const RESULTS = [
  { label: "Ready", style: "bg-lime-300 text-neutral-900", text: "Booked a demo or a meeting, or agreed to buy." },
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
  { icon: "calendar", title: "Results in your portal", text: "Every call's result, demos and meetings show in your portal as soon as a call ends." },
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
  { agent: true, text: "Rajesh ji, yeh plan ₹1,499 per month hai. Kya main aapke liye ek chhota demo book kar doon?" },
  { agent: false, text: "Haan, kal shaam ka rakh dijiye." },
];

const NAV_LINKS = [
  { id: "how", label: "How it works" },
  { id: "call", label: "On a call" },
  { id: "results", label: "Results" },
  { id: "trust", label: "Trust" },
  { id: "enquire", label: "Get started" },
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

export default function Landing({ onLogin, onSignUp }: { onLogin: () => void; onSignUp: () => void }) {
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  const word = useTypewriter(HERO_WORDS);
  useLiquidPointer();

  return (
    <div className="relative isolate min-h-screen text-gray-900">
      <div className="liquid-backdrop" aria-hidden="true">
        {BACKDROP.map((orb, i) => <span key={i} style={orb} />)}
      </div>
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
            <button onClick={() => scrollTo("enquire")} className="btn-bounce shine ml-1 hidden rounded-full bg-lime-300 px-5 py-2 text-sm font-medium text-neutral-900 hover:bg-lime-400 sm:block">Enquire</button>
            <button onClick={onLogin} className="btn-bounce ml-1 rounded-full bg-white/10 px-5 py-2 text-sm font-medium text-white hover:bg-white/20">Login</button>
            <button onClick={onSignUp} className="btn-bounce ml-1 rounded-full bg-white px-5 py-2 text-sm font-medium text-neutral-900 hover:bg-neutral-200">Sign up</button>
          </nav>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 pt-16 pb-12 sm:pt-24 lg:grid-cols-[1.1fr_1fr]">
          <div>
            <p className="rise inline-flex items-center gap-2 rounded-full glass glass-liquid px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-gray-700">
              <span className="breathe h-1.5 w-1.5 rounded-full bg-lime-500" aria-hidden="true" />
              AI voice agent for Indian sales teams
            </p>
            <h1 className="rise mt-5 text-4xl font-medium leading-[1.05] tracking-[-0.035em] text-gray-900 sm:text-6xl" style={delay(80)}
              aria-label="Sales calls in Hinglish, Hindi or English that sound like a person, not a script">
              <span aria-hidden="true">
                Sales calls in{" "}
                {/* The longest word reserves the width, so the headline never jumps while typing */}
                <span className="inline-grid bg-lime-300 px-2 align-baseline">
                  <span className="invisible col-start-1 row-start-1">Hinglish</span>
                  <span className="col-start-1 row-start-1 whitespace-nowrap">{word}<span className="caret h-[0.9em] translate-y-[0.08em]" /></span>
                </span>{" "}
                that sound like a person, not a script
              </span>
            </h1>
            <p className="rise mt-6 max-w-xl text-lg text-gray-600" style={delay(160)}>
              Navix AI calls your leads, answers their questions from your approved details, books demos and
              callbacks, and tells you exactly who is ready to buy.
            </p>
            <div className="rise mt-8 flex flex-wrap gap-3" style={delay(240)}>
              <button onClick={onSignUp} className="btn-bounce shine rounded-full bg-neutral-900 px-6 py-3 font-medium text-white hover:bg-neutral-700">Start free trial</button>
              <button onClick={() => scrollTo("enquire")} className="btn-bounce glass glass-liquid rounded-full px-6 py-3 font-medium text-gray-900">Enquire about services</button>
              <button onClick={() => scrollTo("how")} className="btn-bounce glass glass-liquid rounded-full px-6 py-3 font-medium text-gray-900">See how it works</button>
            </div>
          </div>

          <div className="rise" style={delay(300)}>
            <div className="float">
            <DemoCall fallback={
              <figure className="glass glass-liquid glass-frost rounded-3xl p-5">
                <figcaption className="mb-4 flex items-center justify-between text-xs text-gray-600">
                  <span className="font-mono uppercase tracking-[0.12em]">Example call</span>
                  <span className="rounded-full bg-lime-300 px-2.5 py-0.5 font-medium text-neutral-900">Result: Ready</span>
                </figcaption>
                <ul className="space-y-3">
                  {EXAMPLE.map((line, i) => (
                    <li key={line.text} className={`rise flex ${line.agent ? "" : "justify-end"}`} style={delay(500 + i * 350)}>
                      <p className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm ${line.agent ? "rounded-bl-sm bg-white/45 text-gray-900 ring-1 ring-white/70 backdrop-blur-md" : "rounded-br-sm bg-neutral-900 text-white"}`}>
                        {line.text}
                      </p>
                    </li>
                  ))}
                </ul>
              </figure>
            } />
            </div>
          </div>
        </section>

        <div className="mx-auto max-w-6xl px-4">
          <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {FACTS.map((fact, i) => (
              <div key={fact.value} className="rise lift liquid glass glass-liquid rounded-2xl p-4" style={delay(400 + i * 80)}>
                <Blob />
                <dt className="text-lg font-medium tracking-tight text-gray-900">{fact.value}</dt>
                <dd className="mt-1 text-sm text-gray-600">{fact.label}</dd>
              </div>
            ))}
          </dl>
        </div>

        <Section id="how" eyebrow="How it works" title="From a lead list to people ready to buy">
          <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, i) => (
              <li key={step.title} className="reveal lift liquid glass glass-liquid rounded-3xl p-5">
                <Blob />
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
              <li key={stage.title} className="reveal lift liquid flex gap-4 rounded-3xl bg-neutral-900 p-5 text-white">
                <Blob />
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
              <li key={result.label} className="reveal lift liquid glass glass-liquid rounded-3xl p-5">
                <Blob />
                <span className={`inline-block rounded-full px-3 py-1 text-sm font-medium ${result.style}`}>{result.label}</span>
                <p className="mt-3 text-sm text-gray-600">{result.text}</p>
              </li>
            ))}
          </ul>
        </Section>

        <Section id="features" eyebrow="Features" title="Everything you need to run calling campaigns">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="reveal lift liquid glass glass-liquid rounded-3xl p-5">
                <Blob />
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
              <li key={item.title} className="reveal lift liquid flex gap-4 glass glass-liquid rounded-3xl p-5">
                <Blob />
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

        <Section id="enquire" eyebrow="Get started" title="Tell us about your business"
          intro="Leave your details and we'll take it from there. No payment needed to start the conversation.">
          <div className="grid gap-8 lg:grid-cols-[1fr_1.2fr] lg:items-center">
            <div className="reveal"><Journey /></div>
            <div className="reveal glass glass-liquid glass-frost rounded-3xl p-6 sm:p-8"><EnquiryForm /></div>
          </div>
        </Section>

        <section className="mx-auto max-w-6xl px-4 pb-20">
          <div className="reveal flex flex-wrap items-center justify-between gap-6 rounded-[2rem] bg-neutral-900 p-8 text-white sm:p-12">
            <div className="max-w-xl">
              <h2 className="text-3xl font-medium tracking-[-0.02em]">Pay only for answered minutes</h2>
              <p className="mt-2 text-neutral-400">
                Ringing and unanswered calls cost nothing. Log in to see your calls, results and minutes left.
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <button onClick={onSignUp} className="btn-bounce shine rounded-full bg-lime-300 px-6 py-3 font-medium text-neutral-900 hover:bg-lime-400">Start free trial</button>
              <button onClick={() => scrollTo("enquire")} className="btn-bounce rounded-full bg-white/10 px-6 py-3 font-medium text-white hover:bg-white/20">Enquire</button>
              <button onClick={onLogin} className="btn-bounce rounded-full bg-white/10 px-6 py-3 font-medium text-white hover:bg-white/20">Login</button>
            </div>
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
