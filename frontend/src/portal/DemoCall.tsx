/* ─────────────────────────────────────────────────────────────
 * Landing page demo: any visitor can talk to the real agent in
 * the browser for a couple of minutes, without logging in.
 * ─────────────────────────────────────────────────────────── */
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { get, post } from "./api";
import { useAgentCall } from "./agentCall";
import type { CallTicket } from "./agentCall";
import { Icon } from "./icons";

const TRY_SAYING = ["Price kya hai?", "Bahut mehenga hai", "Demo book karna hai", "Mujhe call mat karna"];

function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function DemoCall({ fallback }: { fallback: ReactNode }) {
  const [info, setInfo] = useState<{ enabled: boolean; max_seconds: number } | null>(null);
  const [left, setLeft] = useState(0);
  const [started, setStarted] = useState(false);
  const call = useAgentCall();
  const ended = started && call.state === "idle" && !call.error;

  useEffect(() => {
    get<{ enabled: boolean; max_seconds: number }>("/demo").then(setInfo).catch(() => setInfo({ enabled: false, max_seconds: 0 }));
  }, []);

  useEffect(() => {
    if (call.state !== "live") return;
    const timer = window.setInterval(() => setLeft((s) => s - 1), 1000);
    return () => window.clearInterval(timer);
  }, [call.state]);

  if (info && !info.enabled) return <>{fallback}</>;

  async function start() {
    setStarted(true);
    await call.start(async () => {
      const ticket = await post<CallTicket & { max_seconds: number }>("/demo/call", {});
      setLeft(ticket.max_seconds);
      return ticket;
    });
  }

  const live = call.state === "live";
  const status = call.state === "connecting" ? "Connecting to Priya…"
    : live ? (call.agentSpeaking ? "Priya is speaking" : "Listening… go ahead and speak")
      : ended ? "Call ended. You can talk again." : "Free demo, no sign-up";

  return (
    <section aria-label="Talk to the agent" className="glass glass-liquid glass-frost rounded-3xl p-6">
      <div className="flex items-center justify-between text-xs text-gray-600">
        <span>Live demo · Hinglish sales call</span>
        {live && <span className="tabular-nums text-gray-700">{clock(left)} left</span>}
      </div>

      <div className="mt-6 flex flex-col items-center text-center">
        <button onClick={live ? call.end : start} disabled={call.state === "connecting" || !info}
          aria-label={live ? "End the call" : "Talk to Priya"}
          className={`relative flex h-28 w-28 items-center justify-center rounded-full text-gray-900 transition-colors disabled:opacity-60 ${
            live ? "bg-red-500 text-white hover:bg-red-600" : "bg-lime-300 hover:bg-lime-400"}`}>
          {live && call.agentSpeaking && <span className="pulse-ring absolute inset-0 rounded-full border-2 border-lime-400" aria-hidden="true" />}
          <Icon name={live ? "close" : "mic"} className="h-10 w-10" />
        </button>
        <p className="mt-4 text-lg font-semibold text-gray-900">{live ? "End call" : "Talk to Priya"}</p>
        <p className="mt-1 text-sm text-gray-600" role="status">{status}</p>

        <div className="mt-4 flex h-8 items-end gap-1" aria-hidden="true">
          {[0.5, 0.8, 1, 0.7, 0.9, 0.6, 1, 0.75, 0.55].map((h, i) => (
            <span key={i} className={`w-1.5 rounded-full ${live && call.agentSpeaking ? "voice-bar bg-lime-600" : "bg-gray-300"}`}
              style={{ height: `${h * 100}%`, animationDelay: `${i * 90}ms` }} />
          ))}
        </div>
      </div>

      {call.error && <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-100/30 px-3 py-2 text-sm text-red-800">{call.error}</p>}

      <div className="mt-6 border-t border-white/70 pt-4">
        <p className="text-xs text-gray-600">Try saying</p>
        <ul className="mt-2 flex flex-wrap gap-2">
          {TRY_SAYING.map((line) => (
            <li key={line} className="rounded-full border border-gray-300 px-3 py-1 text-xs text-gray-700">“{line}”</li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-gray-500">Uses your microphone. The agent says it is an AI assistant at the start of the call.</p>
      </div>
    </section>
  );
}
