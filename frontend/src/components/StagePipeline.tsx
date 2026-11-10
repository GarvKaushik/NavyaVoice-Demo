/* ─────────────────────────────────────────────────────────────
 * StagePipeline — Horizontal stepper showing the current
 * position in the sales conversation lifecycle.
 * ─────────────────────────────────────────────────────────── */
import type { SalesStage } from "../types";

interface Props {
  currentStage: SalesStage;
  endpointDelay: number;
}

const STAGES: { key: SalesStage; label: string; icon: string }[] = [
  { key: "OPENING", label: "Opening", icon: "👋" },
  { key: "DISCOVERY", label: "Discovery", icon: "🔍" },
  { key: "NEED_IDENTIFICATION", label: "Need ID", icon: "🎯" },
  { key: "PITCH", label: "Pitch", icon: "🚀" },
  { key: "OBJECTION_HANDLING", label: "Objections", icon: "🛡️" },
  { key: "CLOSE_NEXT_STEP", label: "Close", icon: "🤝" },
];

export default function StagePipeline({ currentStage, endpointDelay }: Props) {
  const currentIndex = STAGES.findIndex(s => s.key === currentStage);

  return (
    <div className="bg-gray-900/60 backdrop-blur rounded-xl p-4 border border-gray-800">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Sales Pipeline</h3>
        <span className="text-xs bg-gray-800 text-gray-300 px-2 py-0.5 rounded-full">
          VAD Delay: {endpointDelay}ms
        </span>
      </div>

      <div className="flex items-center gap-1">
        {STAGES.map((stage, i) => {
          const isActive = stage.key === currentStage;
          const isPast = i < currentIndex;
          const isFuture = i > currentIndex;

          return (
            <div key={stage.key} className="flex items-center flex-1">
              {/* Stage node */}
              <div className={`flex flex-col items-center flex-1 ${isFuture ? "opacity-40" : ""}`}>
                <div className={`w-9 h-9 rounded-full flex items-center justify-center text-sm transition-all duration-300 ${
                  isActive
                    ? "bg-indigo-600 ring-2 ring-indigo-400 ring-offset-2 ring-offset-gray-950 scale-110"
                    : isPast
                    ? "bg-green-700"
                    : "bg-gray-800"
                }`}>
                  {isPast ? "✓" : stage.icon}
                </div>
                <span className={`text-[10px] mt-1.5 font-medium text-center leading-tight ${
                  isActive ? "text-indigo-300" : isPast ? "text-green-400" : "text-gray-500"
                }`}>
                  {stage.label}
                </span>
              </div>

              {/* Connector line */}
              {i < STAGES.length - 1 && (
                <div className={`h-0.5 w-full min-w-2 mt-[-12px] ${
                  isPast ? "bg-green-700" : "bg-gray-800"
                }`} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
