/* ─────────────────────────────────────────────────────────────
 * App — Main layout for the NavyaNexus AI Voice Agent console.
 * Three-column layout: Conversation | Voice Control | Sidebar
 * ─────────────────────────────────────────────────────────── */
import { useState } from "react";
import { useVoiceAgent } from "./hooks/useVoiceAgent";
import AudioVisualizer from "./components/AudioVisualizer";
import ConversationView from "./components/ConversationView";
import StagePipeline from "./components/StagePipeline";
import PromptEditor from "./components/PromptEditor";
import GuardMonitor from "./components/GuardMonitor";
import MetricsPanel from "./components/MetricsPanel";
import TextInput from "./components/TextInput";

type SidePanel = "prompts" | "monitor";

export default function App() {
  const agent = useVoiceAgent();
  const [sidePanel, setSidePanel] = useState<SidePanel>("prompts");

  return (
    <div className="h-screen flex flex-col bg-gray-950 text-gray-100">
      {/* ── Top Bar ── */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-gray-800 bg-gray-900/80 backdrop-blur shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-xl">🎙️</span>
          <div>
            <h1 className="text-base font-bold text-white tracking-tight">NavyaNexus AI Voice Agent</h1>
            <p className="text-[11px] text-gray-500">Hinglish Sales Calling Console</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Session control button */}
          {!agent.isSessionActive ? (
            <button
              onClick={agent.startSession}
              className="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-500 transition-colors flex items-center gap-2"
            >
              <span className="w-2 h-2 bg-white rounded-full" />
              Start Session
            </button>
          ) : (
            <button
              onClick={agent.stopSession}
              className="px-4 py-2 bg-red-600 text-white text-sm font-medium rounded-lg hover:bg-red-500 transition-colors flex items-center gap-2"
            >
              <span className="w-2 h-2 bg-white rounded-full animate-pulse" />
              End Session
            </button>
          )}
        </div>
      </header>

      {/* ── Stage Pipeline ── */}
      <div className="px-6 py-3 shrink-0">
        <StagePipeline currentStage={agent.currentStage} endpointDelay={agent.endpointDelay} />
      </div>

      {/* ── Main Content Area ── */}
      <div className="flex-1 flex min-h-0 px-6 pb-4 gap-4">
        {/* Left Column: Conversation + Input */}
        <div className="flex-1 flex flex-col min-w-0 bg-gray-900/40 rounded-xl border border-gray-800 overflow-hidden">
          {/* Conversation messages */}
          <div className="flex-1 min-h-0 overflow-y-auto">
            <ConversationView messages={agent.messages} interimText={agent.interimText} />
          </div>

          {/* Voice Visualizer + Text Input */}
          <div className="shrink-0 border-t border-gray-800">
            <div className="flex items-center justify-center py-4">
              <AudioVisualizer audioLevel={agent.audioLevel} vadState={agent.vadState} />
            </div>
            <TextInput
              onSend={agent.sendTextInput}
              disabled={!agent.isSessionActive || agent.vadState === "processing" || agent.vadState === "agent_speaking"}
              placeholder={agent.isSessionActive ? "Type here or speak into your mic..." : "Start a session first..."}
            />
          </div>

          {/* Error display */}
          {agent.error && (
            <div className="mx-4 mb-3 px-3 py-2 bg-red-900/30 border border-red-800 rounded-lg text-xs text-red-300">
              ⚠️ {agent.error}
            </div>
          )}
        </div>

        {/* Right Sidebar */}
        <div className="w-96 shrink-0 flex flex-col gap-4 min-h-0">
          {/* Tab switcher */}
          <div className="flex gap-1 bg-gray-900/60 rounded-lg p-1 border border-gray-800 shrink-0">
            <button
              onClick={() => setSidePanel("prompts")}
              className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                sidePanel === "prompts" ? "bg-indigo-600 text-white" : "text-gray-400 hover:text-white"
              }`}
            >
              📝 Prompt Editor
            </button>
            <button
              onClick={() => setSidePanel("monitor")}
              className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                sidePanel === "monitor" ? "bg-indigo-600 text-white" : "text-gray-400 hover:text-white"
              }`}
            >
              🛡️ Monitor
            </button>
          </div>

          {/* Panel content */}
          <div className="flex-1 min-h-0 overflow-y-auto">
            {sidePanel === "prompts" ? (
              <PromptEditor />
            ) : (
              <div className="space-y-4">
                <GuardMonitor
                  guardStatus={agent.guardStatus}
                  fastPathIntent={agent.fastPathIntent}
                  toolResult={agent.toolResult}
                />
                <MetricsPanel latency={agent.latency} cost={agent.cost} />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
