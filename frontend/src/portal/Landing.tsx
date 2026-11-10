/* ─────────────────────────────────────────────────────────────
 * Public landing page. Sections scroll in place because the hash
 * in the URL is used for page routing.
 * ─────────────────────────────────────────────────────────── */
import type { ReactNode } from "react";
import DemoCall from "./DemoCall";

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 text-white">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center gap-0.5 rounded-lg bg-indigo-500" aria-hidden="true">
        {[8, 14, 10].map((height, i) => (
          <span key={i} className="w-0.5 rounded-full bg-white" style={{ height }} />
        ))}
      </span>
      {compact ? <span className="sr-only">NavyaVoice</span> : <span className="text-base font-semibold tracking-tight">NavyaVoice</span>}
    </span>
  );
}

const STEPS = [
  { title: "Add your product and leads", text: "Share your product details and FAQs, then upload your leads with their consent source." },
  { title: "Test it yourself", text: "Talk to your agent from the browser and hear exactly what your customers will hear." },
  { title: "Start calling", text: "Start a campaign. Busy and unanswered calls are retried, and callbacks happen when customers ask for them." },
  { title: "Follow up on results", text: "See who is ready, who refused, who to contact again and who wants to talk to a manager." },
];

const FEATURES = [
  { title: "Natural Hinglish", text: "Short, warm replies in the Hindi and English mix your customers use on the phone." },
  { title: "Approved answers only", text: "Prices, offers and promises are checked against your approved details before they are spoken." },
  { title: "Open about being AI", text: "Every call opens by telling the customer they are speaking with an AI assistant." },
  { title: "Opt-outs respected", text: "When someone asks not to be called, their number is blocked for your account straight away." },
  { title: "Clear call results", text: "Every call comes with its result, a transcript and a short summary of what to do next." },
  { title: "Your own knowledge", text: "Upload documents so the agent can answer more questions, once they are approved." },
  { title: "Connects to your CRM", text: "Send call results to your CRM automatically as soon as a call ends." },
  { title: "Minutes at a glance", text: "Track minutes used and remaining in your dashboard, and request more when you need them." },
];

const EXAMPLE = [
  { agent: true, text: "Namaste! Main Priya bol rahi hoon, ek AI assistant. Kya meri baat Rajesh ji se ho rahi hai?" },
  { agent: false, text: "Haan boliye, price kya hai?" },
  { agent: true, text: "Rajesh ji, yeh plan ₹1,499 per month hai. Kya main details WhatsApp par bhej doon?" },
  { agent: false, text: "Theek hai, bhej dijiye." },
];

function Section({ id, eyebrow, title, children }: { id: string; eyebrow: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20 mx-auto max-w-6xl px-4 py-16 sm:py-20">
      <p className="text-sm font-medium text-indigo-300">{eyebrow}</p>
      <h2 className="mt-2 max-w-2xl text-2xl font-semibold tracking-tight text-white sm:text-3xl">{title}</h2>
      <div className="mt-10">{children}</div>
    </section>
  );
}

export default function Landing({ onLogin }: { onLogin: () => void }) {
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="sticky top-0 z-10 border-b border-gray-800/70 bg-gray-950/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <Logo />
          <nav className="flex items-center gap-1 sm:gap-3">
            <button onClick={() => scrollTo("how")} className="hidden px-2 py-1 text-sm text-gray-300 hover:text-white sm:block">How it works</button>
            <button onClick={() => scrollTo("features")} className="hidden px-2 py-1 text-sm text-gray-300 hover:text-white sm:block">Features</button>
            <button onClick={onLogin} className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">Login</button>
          </nav>
        </div>
      </header>

      <main>
        <section className="relative overflow-hidden">
          <div className="pointer-events-none absolute inset-x-0 -top-40 h-96 bg-gradient-to-b from-indigo-600/25 to-transparent blur-3xl" aria-hidden="true" />
          <div className="relative mx-auto grid max-w-6xl items-center gap-12 px-4 py-16 sm:py-24 lg:grid-cols-2">
            <div>
              <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-5xl">
                AI sales calls that sound like a real conversation
              </h1>
              <p className="mt-5 max-w-xl text-lg text-gray-300">
                NavyaVoice calls your leads in natural Hinglish, answers their questions from your approved details,
                and shows you exactly who to follow up with.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <button onClick={onLogin} className="rounded-lg bg-indigo-600 px-5 py-2.5 font-medium text-white hover:bg-indigo-500">Login</button>
                <button onClick={() => scrollTo("how")} className="rounded-lg border border-gray-700 px-5 py-2.5 font-medium text-gray-100 hover:bg-gray-800">See how it works</button>
              </div>
            </div>

            <DemoCall fallback={
            <figure className="rounded-2xl border border-gray-800 bg-gray-900/70 p-5 shadow-2xl shadow-indigo-950/50">
              <figcaption className="mb-4 flex items-center justify-between text-xs text-gray-400">
                <span>Example conversation</span>
                <span className="rounded-full border border-green-800 px-2 py-0.5 text-green-300">Result: Ready</span>
              </figcaption>
              <ul className="space-y-3">
                {EXAMPLE.map((line) => (
                  <li key={line.text} className={`flex ${line.agent ? "" : "justify-end"}`}>
                    <p className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm ${line.agent ? "rounded-bl-sm bg-gray-800 text-gray-100" : "rounded-br-sm bg-indigo-600 text-white"}`}>
                      {line.text}
                    </p>
                  </li>
                ))}
              </ul>
            </figure>
            } />
          </div>
        </section>

        <div className="border-t border-gray-800/70">
          <Section id="how" eyebrow="How it works" title="From your lead list to clear results">
            <ol className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {STEPS.map((step, i) => (
                <li key={step.title}>
                  <span className="flex h-8 w-8 items-center justify-center rounded-full border border-indigo-400/50 text-sm font-medium text-indigo-200">{i + 1}</span>
                  <h3 className="mt-3 font-medium text-white">{step.title}</h3>
                  <p className="mt-1.5 text-sm text-gray-400">{step.text}</p>
                </li>
              ))}
            </ol>
          </Section>
        </div>

        <div className="border-t border-gray-800/70 bg-gray-900/30">
          <Section id="features" eyebrow="Features" title="Everything you need to run calling campaigns">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {FEATURES.map((feature) => (
                <div key={feature.title} className="rounded-xl border border-gray-800 bg-gray-900/60 p-4">
                  <h3 className="font-medium text-white">{feature.title}</h3>
                  <p className="mt-1.5 text-sm text-gray-400">{feature.text}</p>
                </div>
              ))}
            </div>
          </Section>
        </div>

        <section className="border-t border-gray-800/70">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-6 px-4 py-14">
            <div>
              <h2 className="text-2xl font-semibold tracking-tight text-white">Already have an account?</h2>
              <p className="mt-1 text-gray-400">Log in to see your calls, results and minutes.</p>
            </div>
            <button onClick={onLogin} className="rounded-lg bg-indigo-600 px-5 py-2.5 font-medium text-white hover:bg-indigo-500">Login</button>
          </div>
        </section>
      </main>

      <footer className="border-t border-gray-800/70">
        <div className="mx-auto max-w-6xl px-4 py-6"><Logo /></div>
      </footer>
    </div>
  );
}
