/* ─────────────────────────────────────────────────────────────
 * TypeScript interfaces matching the backend /api/voice/* and
 * /api/prompts response shapes.
 * ─────────────────────────────────────────────────────────── */

/** Sales pipeline stages (mirrors agent/stages.py SalesStage enum) */
export type SalesStage =
  | "OPENING"
  | "DISCOVERY"
  | "NEED_IDENTIFICATION"
  | "PITCH"
  | "OBJECTION_HANDLING"
  | "CLOSE_NEXT_STEP"
  | "COMPLETED";

/** Guard verification result from the output guard */
export interface GuardStatus {
  passed: boolean;
  authorized_numbers: string[];
  reasons: string[];
}

/** Latency telemetry for a single turn */
export interface LatencyInfo {
  total_turn_ms: number;
}

/** Cost breakdown from CostCalculator */
export interface CostInfo {
  total_inr: number;
  plivo_inr?: number;
  sarvam_stt_inr?: number;
  sarvam_tts_inr?: number;
  llm_inr?: number;
}

/** Response from GET /api/voice/init and POST /api/voice/turn */
export interface VoiceTurnResponse {
  agent_reply: string;
  audio_base64: string | null;
  current_stage: SalesStage;
  endpoint_delay_ms: number;
  guard_status: GuardStatus;
  fast_path_triggered: boolean;
  fast_path_intent: string | null;
  tool_result: Record<string, unknown> | null;
  latency: LatencyInfo;
  cost: CostInfo;
}

/** A single message in the conversation history */
export interface ChatMessage {
  id: string;
  role: "agent" | "user";
  text: string;
  timestamp: number;
  stage?: SalesStage;
  isInterim?: boolean;
}

/** Stage prompt data from /api/prompts GET response */
export interface StagePromptData {
  stage: SalesStage;
  guidance: string;     // read-only guidance from stages.py
  prompt: string;       // editable full stage prompt from seed_data.py
}

/** Objection playbook entry from /api/prompts */
export interface ObjectionPlaybook {
  type: string;         // e.g. "price", "timing", "trust"
  script: string;       // editable approved script
}

/** Full response from GET /api/prompts */
export interface PromptsResponse {
  stage_prompts: StagePromptData[];
  objection_playbooks: ObjectionPlaybook[];
}

/** Request body for POST /api/prompts */
export interface PromptsUpdateRequest {
  stage_prompts?: Record<string, string>;
  objection_playbooks?: Record<string, string>;
}

/** Voice Activity Detection state */
export type VADState = "idle" | "listening" | "speaking" | "processing" | "agent_speaking";
