/* ─────────────────────────────────────────────────────────────
 * A browser call with the real agent worker: joins the LiveKit
 * room the backend created, plays the agent's voice and sends
 * the microphone. Used by the test call and the landing demo.
 * ─────────────────────────────────────────────────────────── */
import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import type { Participant, RemoteTrack } from "livekit-client";

export type CallState = "idle" | "connecting" | "live";

export interface CallTicket {
  url: string;
  token: string;
}

function friendly(error: unknown): string {
  const e = error as Error;
  if (e?.name === "NotAllowedError") return "Allow microphone access in your browser to talk to the agent.";
  if (e?.name === "NotFoundError") return "No microphone was found on this device.";
  return e?.message || "The call could not start. Please try again.";
}

export function useAgentCall() {
  const [state, setState] = useState<CallState>("idle");
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const room = useRef<Room | null>(null);

  useEffect(() => () => void room.current?.disconnect(), []);

  async function start(getTicket: () => Promise<CallTicket>) {
    setError(null);
    setState("connecting");
    try {
      const ticket = await getTicket();
      const lk = new Room();
      room.current = lk;
      lk.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
        if (track.kind !== Track.Kind.Audio) return;
        const el = track.attach();
        el.style.display = "none";
        document.body.appendChild(el);
      });
      lk.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => track.detach().forEach((el) => el.remove()));
      lk.on(RoomEvent.ActiveSpeakersChanged, (speakers: Participant[]) => setAgentSpeaking(speakers.some((p) => !p.isLocal)));
      lk.on(RoomEvent.Disconnected, () => {
        setState("idle");
        setAgentSpeaking(false);
        room.current = null;
      });
      await lk.connect(ticket.url, ticket.token);
      await lk.localParticipant.setMicrophoneEnabled(true);
      setState("live");
    } catch (e) {
      await room.current?.disconnect();
      room.current = null;
      setError(friendly(e));
      setState("idle");
    }
  }

  function end() {
    void room.current?.disconnect();
  }

  return { state, agentSpeaking, error, start, end };
}
