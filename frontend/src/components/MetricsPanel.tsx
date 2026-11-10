/* ─────────────────────────────────────────────────────────────
 * MetricsPanel — Displays turn latency and session cost
 * telemetry from the backend CostCalculator.
 * ─────────────────────────────────────────────────────────── */
import type { VoiceTurnResponse } from "../types";

interface Props {
  latency: number;
  cost: VoiceTurnResponse["cost"] | null;
}

export default function MetricsPanel({ latency, cost }: Props) {
  const latencyColor = latency < 500 ? "text-green-400" : latency < 1000 ? "text-yellow-400" : "text-red-400";

  return (
    <div className="bg-gray-900/60 backdrop-blur rounded-xl p-4 border border-gray-800 space-y-3">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
        📊 Telemetry
      </h3>

      {/* Turn Latency */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-400">Turn Latency</span>
        <span className={`text-sm font-mono font-semibold ${latencyColor}`}>
          {latency > 0 ? `${latency.toFixed(0)}ms` : "—"}
        </span>
      </div>

      {/* Latency bar */}
      <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            latency < 500 ? "bg-green-500" : latency < 1000 ? "bg-yellow-500" : "bg-red-500"
          }`}
          style={{ width: `${Math.min(100, (latency / 2000) * 100)}%` }}
        />
      </div>

      {/* Cost Breakdown */}
      {cost && (
        <div className="space-y-2 pt-2 border-t border-gray-800">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400">Session Cost</span>
            <span className="text-sm font-mono font-semibold text-emerald-400">
              ₹{cost.total_inr.toFixed(2)}
            </span>
          </div>

          {/* Individual cost items */}
          <div className="space-y-1">
            {cost.plivo_inr !== undefined && cost.plivo_inr > 0 && (
              <CostRow label="Plivo Telephony" value={cost.plivo_inr} />
            )}
            {cost.sarvam_stt_inr !== undefined && cost.sarvam_stt_inr > 0 && (
              <CostRow label="Sarvam STT" value={cost.sarvam_stt_inr} />
            )}
            {cost.sarvam_tts_inr !== undefined && cost.sarvam_tts_inr > 0 && (
              <CostRow label="Sarvam TTS" value={cost.sarvam_tts_inr} />
            )}
            {cost.llm_inr !== undefined && cost.llm_inr > 0 && (
              <CostRow label="LLM Inference" value={cost.llm_inr} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Individual cost line item row */
function CostRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] text-gray-500">{label}</span>
      <span className="text-[11px] font-mono text-gray-400">₹{value.toFixed(3)}</span>
    </div>
  );
}
