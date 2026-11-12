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
  const latencyColor = latency < 500 ? "text-green-600" : latency < 1000 ? "text-yellow-600" : "text-red-600";

  return (
    <div className="glass rounded-xl p-4 border border-white/70 space-y-3">
      <h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
        📊 Telemetry
      </h3>

      {/* Turn Latency */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-600">Turn Latency</span>
        <span className={`text-sm font-mono font-semibold ${latencyColor}`}>
          {latency > 0 ? `${latency.toFixed(0)}ms` : "—"}
        </span>
      </div>

      {/* Latency bar */}
      <div className="h-1.5 bg-black/5 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${
            latency < 500 ? "bg-green-500" : latency < 1000 ? "bg-yellow-500" : "bg-red-500"
          }`}
          style={{ width: `${Math.min(100, (latency / 2000) * 100)}%` }}
        />
      </div>

      {/* Cost Breakdown */}
      {cost && (
        <div className="space-y-2 pt-2 border-t border-white/70">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-600">Session Cost</span>
            <span className="text-sm font-mono font-semibold text-emerald-600">
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
      <span className="text-[11px] font-mono text-gray-600">₹{value.toFixed(3)}</span>
    </div>
  );
}
