/* ─────────────────────────────────────────────────────────────
 * ConversationView — Chat-style display of agent and user
 * messages with auto-scroll and interim transcript display.
 * ─────────────────────────────────────────────────────────── */
import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";

interface Props {
  messages: ChatMessage[];
  interimText: string;
}

/** Maps sales stage to a short colored badge */
function StageBadge({ stage }: { stage?: string }) {
  if (!stage) return null;
  const colors: Record<string, string> = {
    OPENING: "bg-blue-900/50 text-blue-300",
    DISCOVERY: "bg-purple-900/50 text-purple-300",
    NEED_IDENTIFICATION: "bg-amber-900/50 text-amber-300",
    PITCH: "bg-emerald-900/50 text-emerald-300",
    OBJECTION_HANDLING: "bg-red-900/50 text-red-300",
    CLOSE_NEXT_STEP: "bg-green-900/50 text-green-300",
    COMPLETED: "bg-gray-700/50 text-gray-400",
  };
  const label = stage.replace(/_/g, " ");
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide ${colors[stage] || "bg-gray-700 text-gray-400"}`}>
      {label}
    </span>
  );
}

export default function ConversationView({ messages, interimText }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, interimText]);

  return (
    <div className="flex flex-col gap-3 p-4 overflow-y-auto h-full">
      {messages.length === 0 && !interimText && (
        <div className="flex items-center justify-center h-full text-gray-500 text-sm">
          Start a session to begin the conversation
        </div>
      )}

      {messages.map((msg) => (
        <div
          key={msg.id}
          className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 ${
            msg.role === "agent"
              ? "bg-gray-800 text-gray-100 rounded-bl-sm"
              : "bg-indigo-600 text-white rounded-br-sm"
          }`}>
            {msg.role === "agent" && msg.stage && (
              <div className="mb-1">
                <StageBadge stage={msg.stage} />
              </div>
            )}
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.text}</p>
            <span className="text-[10px] opacity-50 mt-1 block">
              {new Date(msg.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
          </div>
        </div>
      ))}

      {/* Interim transcript (live speech preview) */}
      {interimText && (
        <div className="flex justify-end">
          <div className="max-w-[80%] rounded-2xl px-4 py-2.5 bg-indigo-600/40 text-indigo-200 rounded-br-sm border border-indigo-500/30">
            <p className="text-sm italic">{interimText}...</p>
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
