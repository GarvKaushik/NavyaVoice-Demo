/* ─────────────────────────────────────────────────────────────
 * AudioVisualizer — Circular mic indicator with real-time
 * amplitude bars and glow effect based on VAD state.
 * ─────────────────────────────────────────────────────────── */
import type { VADState } from "../types";

interface Props {
  audioLevel: number;   // 0.0 to 1.0 normalized amplitude
  vadState: VADState;
}

const STATE_COLORS: Record<VADState, string> = {
  idle: "bg-gray-600",
  listening: "bg-green-500",
  speaking: "bg-emerald-400",
  processing: "bg-yellow-500",
  agent_speaking: "bg-blue-500",
};

const STATE_LABELS: Record<VADState, string> = {
  idle: "Click to Start",
  listening: "Listening...",
  speaking: "You're speaking",
  processing: "Processing...",
  agent_speaking: "Agent speaking",
};

export default function AudioVisualizer({ audioLevel, vadState }: Props) {
  const barCount = 24;
  const bars = Array.from({ length: barCount }, (_, i) => {
    // Create a wave pattern based on audio level
    const angle = (i / barCount) * Math.PI * 2;
    const wave = Math.sin(angle * 3 + Date.now() / 200) * 0.3 + 0.7;
    const height = vadState === "speaking" || vadState === "listening"
      ? Math.max(8, audioLevel * 60 * wave)
      : 8;
    return height;
  });

  const isActive = vadState !== "idle";
  const glowColor = vadState === "speaking" ? "rgba(52, 211, 153, 0.5)"
    : vadState === "listening" ? "rgba(34, 197, 94, 0.3)"
    : vadState === "agent_speaking" ? "rgba(59, 130, 246, 0.4)"
    : "transparent";

  return (
    <div className="flex flex-col items-center gap-3">
      {/* Circular visualizer */}
      <div
        className="relative flex items-center justify-center"
        style={{
          width: 120,
          height: 120,
          borderRadius: "50%",
          boxShadow: isActive ? `0 0 30px ${glowColor}, 0 0 60px ${glowColor}` : "none",
          transition: "box-shadow 0.3s ease",
        }}
      >
        {/* Audio level bars arranged in a circle */}
        <svg width="120" height="120" viewBox="0 0 120 120">
          {bars.map((height, i) => {
            const angle = (i / barCount) * 360;
            const rad = (angle * Math.PI) / 180;
            const innerR = 30;
            const x1 = 60 + innerR * Math.cos(rad);
            const y1 = 60 + innerR * Math.sin(rad);
            const x2 = 60 + (innerR + height) * Math.cos(rad);
            const y2 = 60 + (innerR + height) * Math.sin(rad);
            return (
              <line
                key={i}
                x1={x1} y1={y1} x2={x2} y2={y2}
                stroke={vadState === "speaking" ? "#34d399" : vadState === "agent_speaking" ? "#60a5fa" : "#22c55e"}
                strokeWidth="3"
                strokeLinecap="round"
                opacity={isActive ? 0.8 : 0.3}
              />
            );
          })}
        </svg>

        {/* Center mic icon */}
        <div className={`absolute w-14 h-14 rounded-full flex items-center justify-center ${STATE_COLORS[vadState]} transition-colors duration-300`}>
          {vadState === "processing" ? (
            <svg className="w-6 h-6 text-white animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : (
            <svg className="w-6 h-6 text-white" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
              <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
            </svg>
          )}
        </div>

        {/* Pulse ring when listening */}
        {vadState === "listening" && (
          <div className="absolute w-14 h-14 rounded-full border-2 border-green-400 animate-ping opacity-40" />
        )}
      </div>

      {/* State label */}
      <span className="text-sm font-medium text-gray-400">{STATE_LABELS[vadState]}</span>
    </div>
  );
}
