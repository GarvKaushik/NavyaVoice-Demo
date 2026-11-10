/* ─────────────────────────────────────────────────────────────
 * useVoiceAgent — Central hook managing voice session lifecycle,
 * mic capture with energy VAD, Sarvam STT (via backend), audio
 * playback, and conversation state.
 *
 * Speech recognition runs on Sarvam (same model as phone calls),
 * not the browser's Web Speech API, so no Google services are needed.
 * ─────────────────────────────────────────────────────────── */
import { useState, useCallback, useRef, useEffect } from "react";
import type { VoiceTurnResponse, ChatMessage, VADState, SalesStage } from "../types";
import { initVoiceSession, submitVoiceTurn, streamAudioTurn } from "../api";
import type { TurnStreamEvent } from "../api";

// Unique ID generator for messages
let messageIdCounter = 0;
const nextId = () => `msg-${++messageIdCounter}`;

// ── VAD tuning ──
const TARGET_RATE = 16000;          // WAV sent to Sarvam
const PRE_ROLL_MS = 300;            // keep audio from just before speech starts
const MIN_SPEECH_MS = 300;          // shorter blips are treated as noise
const MAX_UTTERANCE_MS = 15000;     // force end of turn
const EXTRA_SILENCE_MS = 150;       // added to the stage endpointing delay (300/600 ms)
const MIN_THRESHOLD = 0.012;        // RMS floor for speech detection

function downsample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input;
  const ratio = fromRate / toRate;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    // average the samples that fall into this output slot (simple anti-aliasing)
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = sum / Math.max(end - start, 1);
  }
  return out;
}

function encodeWav(samples: Float32Array, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);   // PCM
  view.setUint16(22, 1, true);   // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export function useVoiceAgent() {
  // ── Core State ──
  const [vadState, setVadState] = useState<VADState>("idle");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [currentStage, setCurrentStage] = useState<SalesStage>("OPENING");
  const [endpointDelay, setEndpointDelay] = useState(300);
  const [guardStatus, setGuardStatus] = useState<VoiceTurnResponse["guard_status"] | null>(null);
  const [latency, setLatency] = useState<number>(0);
  const [cost, setCost] = useState<VoiceTurnResponse["cost"] | null>(null);
  const [fastPathIntent, setFastPathIntent] = useState<string | null>(null);
  const [toolResult, setToolResult] = useState<Record<string, unknown> | null>(null);
  const [interimText, setInterimText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSessionActive, setIsSessionActive] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);

  // ── Refs ──
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const isProcessingRef = useRef(false);
  const animFrameRef = useRef<number>(0);
  const endpointDelayRef = useRef(endpointDelay);
  const playingAudioRef = useRef<HTMLAudioElement | null>(null);
  const callEndedRef = useRef(false);
  const currentStageRef = useRef<SalesStage>("OPENING");

  // VAD capture state
  const listeningRef = useRef(false);
  const speakingRef = useRef(false);
  const chunksRef = useRef<Float32Array[]>([]);
  const preRollRef = useRef<Float32Array[]>([]);
  const speechStartRef = useRef(0);
  const silenceStartRef = useRef<number | null>(null);
  const noiseFloorRef = useRef(0.005);

  // Keep ref in sync with state
  useEffect(() => { endpointDelayRef.current = endpointDelay; }, [endpointDelay]);

  const resumeListening = useCallback(() => {
    if (callEndedRef.current) {
      setVadState("idle");
      return;
    }
    speakingRef.current = false;
    chunksRef.current = [];
    silenceStartRef.current = null;
    listeningRef.current = true;
    setVadState("listening");
  }, []);

  // ── Process API response helper ──
  const handleApiResponse = useCallback((data: VoiceTurnResponse & { call_ended?: boolean }) => {
    if (data.agent_reply) {
      setMessages(prev => [...prev, {
        id: nextId(),
        role: "agent",
        text: data.agent_reply,
        timestamp: Date.now(),
        stage: data.current_stage,
      }]);
    }

    setCurrentStage(data.current_stage);
    currentStageRef.current = data.current_stage;
    setEndpointDelay(data.endpoint_delay_ms);
    setGuardStatus(data.guard_status);
    setLatency(data.latency.total_turn_ms);
    setCost(data.cost);
    setFastPathIntent(data.fast_path_intent);
    setToolResult(data.tool_result);
    if (data.call_ended) callEndedRef.current = true;

    if (data.audio_base64) {
      listeningRef.current = false; // mic muted while the agent speaks (no self-transcription)
      setVadState("agent_speaking");
      const audio = new Audio(`data:audio/wav;base64,${data.audio_base64}`);
      playingAudioRef.current = audio;
      const done = () => { playingAudioRef.current = null; resumeListening(); };
      audio.onended = done;
      audio.onerror = done;
      audio.play().catch(done);
    } else {
      resumeListening();
    }
  }, [resumeListening]);

  // ── Submit user transcript to backend ──
  const submitTranscript = useCallback(async (text: string) => {
    if (isProcessingRef.current || !text.trim()) return;
    isProcessingRef.current = true;
    listeningRef.current = false;
    setVadState("processing");
    setInterimText("");

    setMessages(prev => [...prev, {
      id: nextId(),
      role: "user",
      text: text.trim(),
      timestamp: Date.now(),
    }]);

    try {
      const response = await submitVoiceTurn(text.trim());
      handleApiResponse(response);
    } catch (err) {
      setError(`Failed to process turn: ${err}`);
      resumeListening();
    } finally {
      isProcessingRef.current = false;
    }
  }, [handleApiResponse, resumeListening]);

  // ── Sequential playback of streamed sentence audio ──
  const audioQueueRef = useRef<string[]>([]);
  const streamDoneRef = useRef(true);

  const playNext = useCallback(() => {
    if (playingAudioRef.current) return;
    const next = audioQueueRef.current.shift();
    if (!next) {
      if (streamDoneRef.current) resumeListening();
      return;
    }
    setVadState("agent_speaking");
    const audio = new Audio(`data:audio/wav;base64,${next}`);
    playingAudioRef.current = audio;
    const done = () => { playingAudioRef.current = null; playNext(); };
    audio.onended = done;
    audio.onerror = done;
    audio.play().catch(done);
  }, [resumeListening]);

  // ── End of utterance: encode, stream to backend (Sarvam STT -> agent -> Bulbul), play as it arrives ──
  const finishUtterance = useCallback(async (sampleRate: number) => {
    listeningRef.current = false;
    speakingRef.current = false;
    isProcessingRef.current = true;
    const chunks = chunksRef.current;
    chunksRef.current = [];
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const merged = new Float32Array(total);
    let offset = 0;
    for (const c of chunks) { merged.set(c, offset); offset += c.length; }

    setVadState("processing");
    setInterimText("Transcribing (Sarvam)…");
    streamDoneRef.current = false;
    audioQueueRef.current = [];
    const agentMsgId = nextId();
    let agentText = "";

    const onEvent = (ev: TurnStreamEvent) => {
      if (ev.type === "transcript") {
        setInterimText("");
        if (ev.text) {
          setMessages(prev => [...prev, { id: nextId(), role: "user", text: ev.text, timestamp: Date.now() }]);
        }
      } else if (ev.type === "sentence") {
        agentText = agentText ? `${agentText} ${ev.text}` : ev.text;
        const text = agentText;
        setMessages(prev => {
          const others = prev.filter(m => m.id !== agentMsgId);
          return [...others, { id: agentMsgId, role: "agent", text, timestamp: Date.now(), stage: currentStageRef.current }];
        });
        if (ev.audio_base64) {
          audioQueueRef.current.push(ev.audio_base64);
          playNext();
        }
      } else if (ev.type === "done") {
        if (ev.current_stage) {
          setCurrentStage(ev.current_stage);
          currentStageRef.current = ev.current_stage;
          setMessages(prev => prev.map(m => (m.id === agentMsgId ? { ...m, stage: ev.current_stage } : m)));
        }
        if (ev.endpoint_delay_ms) setEndpointDelay(ev.endpoint_delay_ms);
        if (ev.guard_status) setGuardStatus(ev.guard_status);
        if (ev.latency) setLatency((ev.latency as { first_audio_ms?: number }).first_audio_ms ?? ev.latency.total_turn_ms);
        if (ev.cost) setCost(ev.cost);
        setFastPathIntent(ev.fast_path_intent ?? null);
        setToolResult(ev.tool_result ?? null);
        if (ev.call_ended) callEndedRef.current = true;
      }
    };

    try {
      const wav = encodeWav(downsample(merged, sampleRate, TARGET_RATE), TARGET_RATE);
      await streamAudioTurn(wav, onEvent);
    } catch (err) {
      setInterimText("");
      setError(`${err}`);
    } finally {
      isProcessingRef.current = false;
      streamDoneRef.current = true;
      if (!playingAudioRef.current) playNext();
    }
  }, [playNext]);

  // ── Mic capture with energy VAD ──
  const startAudioCapture = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    streamRef.current = stream;

    const audioCtx = new AudioContext();
    audioContextRef.current = audioCtx;
    const source = audioCtx.createMediaStreamSource(stream);

    // Level meter for the visualizer
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.8;
    source.connect(analyser);
    analyserRef.current = analyser;
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    const updateLevel = () => {
      analyser.getByteFrequencyData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) sum += dataArray[i] * dataArray[i];
      setAudioLevel(Math.sqrt(sum / dataArray.length) / 255);
      animFrameRef.current = requestAnimationFrame(updateLevel);
    };
    updateLevel();

    // Raw PCM capture
    const processor = audioCtx.createScriptProcessor(2048, 1, 1);
    processorRef.current = processor;
    const rate = audioCtx.sampleRate;
    const chunkMs = (2048 / rate) * 1000;
    const preRollChunks = Math.ceil(PRE_ROLL_MS / chunkMs);

    processor.onaudioprocess = (e) => {
      if (!listeningRef.current) return;
      const input = new Float32Array(e.inputBuffer.getChannelData(0));
      let sum = 0;
      for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
      const rms = Math.sqrt(sum / input.length);
      const threshold = Math.max(MIN_THRESHOLD, noiseFloorRef.current * 3);
      const now = performance.now();

      if (!speakingRef.current) {
        noiseFloorRef.current = noiseFloorRef.current * 0.95 + rms * 0.05; // adapt to room noise
        preRollRef.current.push(input);
        if (preRollRef.current.length > preRollChunks) preRollRef.current.shift();
        if (rms > threshold) {
          speakingRef.current = true;
          speechStartRef.current = now;
          silenceStartRef.current = null;
          chunksRef.current = [...preRollRef.current];
          preRollRef.current = [];
          setVadState("speaking");
          setInterimText("Listening…");
        }
        return;
      }

      chunksRef.current.push(input);
      const spokenMs = now - speechStartRef.current;
      if (rms < threshold) {
        if (silenceStartRef.current === null) silenceStartRef.current = now;
        const silentFor = now - silenceStartRef.current;
        if (silentFor > endpointDelayRef.current + EXTRA_SILENCE_MS) {
          if (spokenMs - silentFor < MIN_SPEECH_MS) {
            // too short: noise, not speech
            speakingRef.current = false;
            chunksRef.current = [];
            setVadState("listening");
            setInterimText("");
            return;
          }
          void finishUtterance(rate);
        }
      } else {
        silenceStartRef.current = null;
      }
      if (spokenMs > MAX_UTTERANCE_MS) void finishUtterance(rate);
    };

    source.connect(processor);
    processor.connect(audioCtx.destination); // required for onaudioprocess to fire (outputs silence)
  }, [finishUtterance]);

  const stopAudioCapture = useCallback(() => {
    listeningRef.current = false;
    if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    if (processorRef.current) {
      processorRef.current.onaudioprocess = null;
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    if (playingAudioRef.current) {
      playingAudioRef.current.pause();
      playingAudioRef.current = null;
    }
    setAudioLevel(0);
  }, []);

  // ── Start Session ──
  const startSession = useCallback(async () => {
    setError(null);
    setMessages([]);
    setVadState("processing");
    setIsSessionActive(true);
    callEndedRef.current = false;

    try {
      await startAudioCapture();
      const initData = await initVoiceSession();
      handleApiResponse(initData);
    } catch (err) {
      setError(`Failed to start session: ${err}`);
      stopAudioCapture();
      setVadState("idle");
      setIsSessionActive(false);
    }
  }, [startAudioCapture, stopAudioCapture, handleApiResponse]);

  // ── Stop Session ──
  const stopSession = useCallback(() => {
    stopAudioCapture();
    setVadState("idle");
    setInterimText("");
    setIsSessionActive(false);
    isProcessingRef.current = false;
  }, [stopAudioCapture]);

  // ── Manual text input ──
  const sendTextInput = useCallback(async (text: string) => {
    if (!text.trim() || isProcessingRef.current) return;
    await submitTranscript(text);
  }, [submitTranscript]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopSession();
    };
  }, [stopSession]);

  return {
    // State
    vadState, messages, currentStage, endpointDelay, guardStatus,
    latency, cost, fastPathIntent, toolResult, interimText, error,
    isSessionActive, audioLevel,
    // Actions
    startSession, stopSession, sendTextInput,
  };
}
