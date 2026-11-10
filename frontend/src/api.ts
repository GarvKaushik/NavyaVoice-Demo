/* ─────────────────────────────────────────────────────────────
 * API Client — thin fetch wrappers for the FastAPI backend.
 * All endpoints are proxied through Vite dev server at /api/*.
 * ─────────────────────────────────────────────────────────── */

import type { VoiceTurnResponse, PromptsResponse, PromptsUpdateRequest } from "./types";

const API_BASE = "/api";

/**
 * Initialize a new voice session.
 * Returns the opening greeting with TTS audio.
 */
export async function initVoiceSession(): Promise<VoiceTurnResponse> {
  const response = await fetch(`${API_BASE}/voice/init`);
  if (!response.ok) {
    throw new Error(`Failed to initialize voice session: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Submit a user's speech transcript for processing.
 * Returns agent reply with TTS audio, guard status, and stage info.
 */
export async function submitVoiceTurn(transcript: string): Promise<VoiceTurnResponse> {
  const response = await fetch(`${API_BASE}/voice/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcript }),
  });
  if (!response.ok) {
    throw new Error(`Voice turn failed: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Transcribe one recorded utterance (16 kHz WAV) with Sarvam STT via the backend.
 */
export async function transcribeAudio(wav: Blob): Promise<{ transcript: string; latency_ms: number }> {
  const response = await fetch(`${API_BASE}/voice/stt`, {
    method: "POST",
    headers: { "Content-Type": "audio/wav" },
    body: wav,
  });
  if (!response.ok) {
    throw new Error(`Speech-to-text failed: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

/** One line of the streamed audio turn. */
export type TurnStreamEvent =
  | { type: "transcript"; text: string; stt_ms: number }
  | { type: "sentence"; text: string; kind: string; cached: boolean; guard_reasons: string[]; audio_base64: string | null }
  | ({ type: "done"; empty?: boolean; call_ended?: boolean } & Partial<VoiceTurnResponse>);

/**
 * Send one recorded utterance; receive transcript, then each reply sentence
 * with audio as soon as it is ready, then turn metrics (NDJSON stream).
 */
export async function streamAudioTurn(wav: Blob, onEvent: (e: TurnStreamEvent) => void): Promise<void> {
  const response = await fetch(`${API_BASE}/voice/turn_audio`, {
    method: "POST",
    headers: { "Content-Type": "audio/wav" },
    body: wav,
  });
  if (!response.ok || !response.body) {
    throw new Error(`Voice turn failed: ${response.status} ${await response.text()}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as TurnStreamEvent);
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as TurnStreamEvent);
}

/**
 * Fetch current stage prompts and objection playbooks.
 */
export async function fetchPrompts(): Promise<PromptsResponse> {
  const response = await fetch(`${API_BASE}/prompts`);
  if (!response.ok) {
    throw new Error(`Failed to fetch prompts: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Update stage prompts and/or objection playbooks in-memory.
 */
export async function updatePrompts(data: PromptsUpdateRequest): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/prompts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(`Failed to update prompts: ${response.statusText}`);
  }
  return response.json();
}
