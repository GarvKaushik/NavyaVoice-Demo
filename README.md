# Hinglish AI Sales Calling Agent

Outbound Hinglish sales agent: LiveKit Agents (self-hosted) + Sarvam streaming STT + an India-hosted LLM with
server-validated tools + Bulbul v4 Flash TTS, with a fast-path guard for opt-outs and an output guard that stops
any price, date, claim or promise that did not come from a tool.

## How a turn works

```
caller audio -> Sarvam STT (saaras:v3, translit, 8 kHz) -> lexicon fix
  -> backchannel filter ("haan", "hmm" never interrupt)
  -> fast-path guard (opt-out, call later, wrong person, abuse, human, complaint) -> scripted cached line, LLM skipped
  -> stage manager (code owns the sales stage) + observer (async, never blocks)
  -> stage prompt -> LLM with tools (get_price, get_offer, send_payment_link, book_demo, ...)
  -> sentence buffer -> output guard per sentence -> Bulbul v4 Flash (phrase + sentence cache)
```

All of this lives in `agent/dialogue.py` (`DialogueEngine`), which phone calls, the browser console and tests share.

## Run it locally

```powershell
pip install -r requirements.txt
docker compose up -d postgres redis livekit      # dev services
python -m database.seed_data --reset             # schema + approved catalog, offers, playbook, demo slots
python -m speech.prerender --wav assets\phrases\listen   # render cached lines (listen to the .wav files)
```

## Test it

| What | Command |
| --- | --- |
| **Talk to the agent with your mic and speakers** (real STT + LLM + TTS, no phone) | `python -m agent.worker console` |
| Text chat with the real LLM (shows tools, stages, guard, latency) | `python -m scripts.chat` |
| Browser console (React UI, Bulbul audio) | `uvicorn campaign.api:app --port 8000` and `cd frontend; npm run dev` -> http://localhost:5173 |
| API docs (all endpoints) | http://localhost:8000/docs |
| Hear any sentence in the campaign voice | `POST /api/tts {"text": "..."}` |
| Unit + engine tests (no network) | `python -m pytest` |

Things worth trying in a conversation: ask the price, say it is expensive, push for "999 mein do",
ask for a demo and pick a slot, ask for the WhatsApp link, say "abhi busy hoon, kal shaam 5 baje call karna",
say "mujhe dobara call mat karna", say "haan" while the agent is speaking.

Voice and model are `.env` settings: `BULBUL_TTS_VOICE_ID` (e.g. `simran_enhi_customer`, `ritu_hi_sales`,
`ishita_enhi_customer`), `BULBUL_TTS_PACE`, `REALTIME_LLM_MODEL`. Re-run `python -m speech.prerender --force`
after changing the voice.

In `development`, payment links and WhatsApp/SMS are sandboxed (logged, not sent) unless Razorpay/Plivo
messaging keys are set. Calls are stored in Postgres: `GET /calls`, `GET /calls/{id}` (transcript, events, cost).

## Real phone calls

Requires the LiveKit SIP service and a Plivo Zentrunk outbound trunk registered in LiveKit
(`LIVEKIT_SIP_OUTBOUND_TRUNK_ID`). Then run `python -m agent.worker dev` and
`POST /calls/test {"phone_number": "+91..."}`.

## Layout

```
agent/        dialogue engine, stages, prompts, tools, observer, knowledge snapshot, LiveKit worker
guardrails/   fast-path guard, backchannel filter, output guard, authorization context
speech/       normalizer, Hindi number words, lexicon, phrase cache, prerender, Bulbul REST client
llm/          OpenAI-compatible streaming client (first-token timeout, failover, circuit breakers)
campaign/     control API, dialer (LiveKit dispatch), compliance checks
integrations/ payment links (Razorpay), WhatsApp/SMS (Plivo)
database/     models (Postgres + pgvector), session, seed
observability/ metrics, cost calculator, call recorder, tracing, logging
scripts/      chat
tests/        guards, normalizer, stages/tools/cost, engine (fake LLM)
```
