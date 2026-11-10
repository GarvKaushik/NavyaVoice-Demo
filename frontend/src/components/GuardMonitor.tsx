/* ─────────────────────────────────────────────────────────────
 * GuardMonitor — Real-time display of output guard results,
 * fast-path intent detection, and authorized numbers.
 * ─────────────────────────────────────────────────────────── */
import type { VoiceTurnResponse } from "../types";

interface Props {
  guardStatus: VoiceTurnResponse["guard_status"] | null;
  fastPathIntent: string | null;
  toolResult: Record<string, unknown> | null;
}

export default function GuardMonitor({ guardStatus, fastPathIntent, toolResult }: Props) {
  return (
    <div className="bg-gray-900/60 backdrop-blur rounded-xl p-4 border border-gray-800 space-y-3">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
        🛡️ Guard Monitor
      </h3>

      {/* Output Guard Status */}
      <div className="flex items-center gap-2">
        <div className={`w-2.5 h-2.5 rounded-full ${
          guardStatus === null ? "bg-gray-600" : guardStatus.passed ? "bg-green-500" : "bg-red-500"
        }`} />
        <span className="text-sm text-gray-300">
          Output Guard: {guardStatus === null ? "Waiting" : guardStatus.passed ? "Passed ✓" : "Blocked ✗"}
        </span>
      </div>

      {/* Guard rejection reasons */}
      {guardStatus && !guardStatus.passed && guardStatus.reasons.length > 0 && (
        <div className="bg-red-900/20 border border-red-800 rounded-lg px-3 py-2">
          <p className="text-xs text-red-300 font-medium mb-1">Rejection Reasons:</p>
          <ul className="text-xs text-red-400 space-y-0.5">
            {guardStatus.reasons.map((r, i) => (
              <li key={i}>• {r}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Authorized Numbers */}
      {guardStatus && guardStatus.authorized_numbers.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 mb-1">Authorized Numbers:</p>
          <div className="flex flex-wrap gap-1">
            {guardStatus.authorized_numbers.map((n) => (
              <span key={n} className="text-[10px] bg-gray-800 text-gray-300 px-2 py-0.5 rounded-full font-mono">
                {n}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Fast Path Intent */}
      <div className="flex items-center gap-2">
        <div className={`w-2.5 h-2.5 rounded-full ${fastPathIntent ? "bg-amber-500" : "bg-gray-600"}`} />
        <span className="text-sm text-gray-300">
          Fast-Path: {fastPathIntent ? (
            <span className="text-amber-300 font-medium">{fastPathIntent}</span>
          ) : "None"}
        </span>
      </div>

      {/* Tool Result */}
      {toolResult && (
        <div className="bg-blue-900/20 border border-blue-800 rounded-lg px-3 py-2">
          <p className="text-xs text-blue-300 font-medium mb-1">🔧 Tool Result:</p>
          <pre className="text-[11px] text-blue-200 whitespace-pre-wrap overflow-x-auto font-mono">
            {JSON.stringify(toolResult, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
