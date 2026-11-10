# Hinglish AI Sales Calling Agent

Outbound Hinglish sales agent: LiveKit Agents (self-hosted) + Sarvam streaming STT + an India-hosted LLM with
server-validated tools + Bulbul v4 Flash TTS, with a fast-path guard for opt-outs and an output guard that stops
any price, date, claim or promise that did not come from a tool.

New here? [SETUP.md](SETUP.md) has every step to run the project, the STT and TTS services, and phone calls through
Twilio or Plivo.

## Roadmap

Multi-client portal, in build order. Each stage depends on the ones above it.

| # | Stage | Scope |
| --- | --- | --- |
| 1 | Shorter replies | About 18 words per reply, up to 25 when explaining the product or answering a detailed question; shorter approved answers |
| 2 | Client separation | Alembic migrations, clients and users, email login, client and admin roles, all data scoped by client, internal costs hidden from clients |
| 3 | Per-client calling | Company name, persona, caller ID, voice and product content per client; no seed-content fallback in production |
| 4 | Billing and limits | Calls timed from answer, minutes ledger, billing rule in plan settings, credit requests approved by an admin, new dials pause at zero |
| 5 | Dialer and opt-outs | Separate dialer service for campaigns, retries and callbacks; global calls-per-second limit; per-client concurrent call cap; per-client opt-outs; CRM webhook sender |
| 6 | Call results | Client-facing outcome labels, post-call summaries, audio recording through LiveKit Egress (off by default) |
| 7 | Knowledge base | Document upload and processing, admin approval, approved content available on calls |
| 8 | Dashboards | Login, admin console, client portal, browser test call through the real worker |

Agreed product rules:

- Clients are billed per answered minute. The rule lives in plan settings because it may change.
- An opt-out blocks a number for that client only. National DND registry entries apply to every client.
- A client out of minutes requests more credits and an admin raises the quota. The current call finishes and new dials resume after the top-up.
- Defaults: 5 concurrent calls per client (admin can change), 30 free browser test minutes per client per month.

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
python -m alembic upgrade head                   # schema
python -m database.seed_data                     # demo client, approved catalog, offers, playbook, demo slots
python -m scripts.create_admin you@example.com   # your admin login for the portal
python -m speech.prerender --wav assets\phrases\listen   # render cached lines (listen to the .wav files)
```

A database created before migrations existed needs `python -m alembic stamp 0001` once, then `upgrade head`.
Its existing rows move to the demo client.

## Run the portal

```powershell
python -m uvicorn campaign.api:app --port 8000     # API, and the portal at http://localhost:8000 after a frontend build
python -m agent.worker start             # call worker, also used by browser test calls
python -m campaign.background            # dialer, CRM webhooks, post-call summaries, recording retention
cd frontend; npm run dev                 # portal with hot reload at http://localhost:5173
```

Sign in as the admin, add a plan and a client, set the client's products and script, and create a login for them.
Campaigns only dial while `campaign.background` is running.

The website has three parts:

- **Landing page** with a live demo: any visitor presses **Talk to Priya** and talks to the real agent in the browser,
  without logging in. It uses the `DEMO_CAMPAIGN_ID` script, ends politely after `DEMO_MAX_SECONDS`, and is limited to
  `DEMO_CALLS_PER_HOUR` per visitor and `DEMO_MAX_CONCURRENT` overall. `DEMO_CALL_ENABLED=false` switches it off.
- **Client portal** and **admin console**, both with a left sidebar. The client sidebar ends with minutes left and
  a link to request more; the admin sidebar ends with the number of approvals waiting.

Before going live, set `AUTH_SECRET`. Recording also needs a LiveKit Egress service and a bucket, and stays off until
`RECORDING_ENABLED=true`. Each client's number must be on the Plivo trunk and the LiveKit outbound trunk.

## Test it

| What | Command |
| --- | --- |
| **Talk to the agent with your mic and speakers** (real STT + LLM + TTS, no phone) | `python -m agent.worker console` |
| Text chat with the real LLM (shows tools, stages, guard, latency) | `python -m scripts.chat` |
| Script console (React UI, Bulbul audio, admin login) | Portal -> Script console |
| Talk to the agent as a website visitor (no login) | Landing page -> Talk to Priya |
| Browser test call through the real worker | Portal -> Test & call -> In this browser |
| Real phone call to any number (needs a calling provider) | Portal -> Test & call -> On a phone |
| API docs (all endpoints) | http://localhost:8000/docs |
| Hear any sentence in the campaign voice | `POST /api/tts {"text": "..."}` |
| Unit + engine + portal tests (no network) | `python -m pytest` |
| The same tests on a throwaway Postgres database | set `TEST_DATABASE_URL`, then `python -m pytest` |

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

Clients can also call any number from **Test & call -> On a phone** (`POST /api/client/phone-call`). The number can be
typed as `+91 98765 43210`, `098765 43210` or `9876543210`; it is saved as a lead of the chosen campaign, and answered
time uses the client's minutes. Calling hours and the DND list still apply. Until `LIVEKIT_SIP_OUTBOUND_TRUNK_ID` is
set, the page says phone calling isn't set up yet. The calling provider is not chosen yet: the code only needs a SIP
trunk in LiveKit, so Plivo or Twilio can be connected without code changes.

## Speech services

STT, TTS and the pipeline are three separate services. The pipeline (API, worker, portal, LLM client) reaches the
other two only through links in its `.env`, so each one can run on this machine, a GPU laptop or Modal, and move
without code changes.

| Service | Start it | Pipeline `.env` |
| --- | --- | --- |
| STT (IndicConformer, Whisper-Hinglish) | `python -m speech.stt_server --host 0.0.0.0` | `STT_SERVER_URL=wss://<host>/v1/stt` |
| TTS (Maya 2 Native, Svara as fallback) | `python -m speech.tts_server --host 0.0.0.0` | `TTS_PROVIDER=server`, `TTS_SERVER_URL=wss://<host>/v1/tts` |

Set the same `SPEECH_SERVER_TOKEN` on every service and in the pipeline. Without it, anyone who finds a service's
link can use it.

How they talk:

- **STT:** one WebSocket per call. 16-bit 8 kHz PCM streams in as the customer speaks; the pipeline's voice
  detection decides when they have finished and asks for the final transcript. If the connection drops, the
  utterance is sent again whole on a new one. The server sends nothing on connect and replies only to `flush`
  (or `partial`); the message format is at the top of `speech/stt_server/app.py`.
- **TTS:** one WebSocket per call. Each sentence has a `turn_id`; its audio streams back at 8 kHz as it is made.
  When the customer interrupts, the pipeline sends `cancel` and the service stops generating at once. If Maya fails
  before a sentence's first audio, Svara speaks that sentence (Devanagari, "।" for full stops, a `<happy>` tag,
  1.1x speed). Bulbul can be a last fallback in the pipeline with `TTS_FALLBACK_TO_SARVAM=true`.

### Self-host STT or TTS and share a link

One person hosts a service on their machine and sends back a link; the person running the pipeline puts that link in
their `.env`. Both sides use the same `SPEECH_SERVER_TOKEN`: the pipeline owner generates it
(`python -c "import secrets; print(secrets.token_urlsafe(32))"`) and sends it privately, never in the repo or a chat
with the link.

**1. Set up the host machine**

```powershell
git clone <this repo>; cd <repo folder>
python -m venv .venv; .venv\Scripts\activate          # Linux/WSL: python3 -m venv .venv; source .venv/bin/activate
pip install -r requirements.txt -r requirements-local.txt
copy .env.example .env                                 # Linux/WSL: cp .env.example .env
```

In `.env` set `SPEECH_SERVER_TOKEN=<the token from the pipeline owner>`.

**2a. Start STT** (any laptop; Windows or Linux; a CPU is enough)

1. Accept the terms of `ai4bharat/indic-conformer-600m-multilingual` on Hugging Face, create a read token, and set
   `HF_TOKEN=<it>` in `.env`. The model downloads once, on the first request.
2. Start it, and keep this terminal open:
   ```powershell
   python -m speech.stt_server --port 8765 --log-level info
   ```
3. Check http://127.0.0.1:8765/health shows `"status":"ok"`.

**2b. Start TTS** (Linux, or Windows with WSL2 Ubuntu; an NVIDIA GPU with about 10 GB or more; Python 3.10-3.12)

1. Install the voices:
   ```bash
   pip install -r requirements-tts-server.txt --extra-index-url https://download.pytorch.org/whl/cu128
   CMAKE_ARGS="-DGGML_CUDA=on" pip install --force-reinstall --no-deps --no-cache-dir llama-cpp-python
   ```
2. Svara is the fallback voice. Copy `svara-tts-v1-Q3_K_M.gguf` (ask the pipeline owner) into `models/svara/`, or set
   `TTS_FALLBACK_ENGINE=` in `.env` to run Maya alone.
3. In `.env`: `TTS_ENGINE=maya`, `TTS_FALLBACK_ENGINE=svara`, `TTS_MAYA_VOICE=Ahana`.
4. Start it, and keep this terminal open:
   ```bash
   python -m speech.tts_server --port 8766 --log-level info
   ```
5. The first start downloads Maya (about 5 GB). Check http://127.0.0.1:8766/health until it shows
   `"loaded": ["maya"]`.

Without a GPU, `TTS_ENGINE=piper` and `TTS_FALLBACK_ENGINE=` run a robotic CPU voice, which is enough to test a link.

**3. Get a public link with a tunnel** (one tunnel per service, in its own terminal)

With ngrok (free account at ngrok.com):

```powershell
winget install ngrok.ngrok                             # Linux/WSL: download from https://ngrok.com/download
ngrok config add-authtoken <your ngrok authtoken>      # once; from the ngrok dashboard; keep it private
ngrok http 8766                                        # TTS; for STT: ngrok http 8765
```

If Windows says `ngrok` isn't recognised after installing, open a new terminal, or run
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe` directly.

ngrok prints a line like `Forwarding  https://garbage-backhand-dropout.ngrok-free.dev -> http://localhost:8766`.
Turn that into the service link: replace `https://` with `wss://` and add the path.

| Service | Link to share |
| --- | --- |
| STT | `wss://<name>.ngrok-free.dev/v1/stt` |
| TTS | `wss://<name>.ngrok-free.dev/v1/tts` |

The link changes every time ngrok restarts. ngrok's free plan includes one fixed domain (dashboard -> Domains); start
with `ngrok http --url=<your-domain>.ngrok-free.dev 8766` to keep the same link. Without an ngrok account,
`cloudflared tunnel --url http://localhost:8766` gives a `https://<words>.trycloudflare.com` link that works the same
way (`wss://<words>.trycloudflare.com/v1/tts`); run one per service if you host both.

**4. Check the link, then share it**

```powershell
python -m scripts.speech_check tts --url wss://<link>/v1/tts      # writes tts_check.wav: listen to it
python -m scripts.speech_check stt --url wss://<link>/v1/stt      # speak for 5 s, prints the transcript
```

Send the link to the pipeline owner (the token, they already have). Keep the service and tunnel terminals open and the
machine awake for as long as the link is in use. Ctrl+C in the tunnel terminal cuts off access at once.

Logs: the service terminal shows each connection (`[accepted]`, or `403` for a wrong token) and each utterance;
http://127.0.0.1:4040 lists every request through ngrok.

**5. Use a shared link** (the pipeline side)

```ini
TTS_PROVIDER=server
TTS_SERVER_URL=wss://<their link>/v1/tts
STT_SERVER_URL=wss://<their link>/v1/stt
SPEECH_SERVER_TOKEN=<the same token>
TTS_SERVER_VOICE=maya-ahana        # cached audio is filed under this name; change it when the voice changes
```

Check it with the same `python -m scripts.speech_check` commands, restart the worker and the API, and run
`python -m speech.prerender --force` so the cached lines are made in the new voice.

For IndicConformer on a GPU, swap `onnxruntime` for `onnxruntime-gpu` and set `STT_INDICCONFORMER_DEVICE=cuda`.
Whisper-Hinglish (`STT_PROVIDER=whisper_hinglish`) needs a converted model in `models/whisper-hinglish-apex-ct2`,
which this README doesn't cover; IndicConformer, the default, doesn't need it.

### Deploy STT and TTS on Modal

`deploy/modal_stt.py` (CPU) and `deploy/modal_tts.py` (T4 GPU) deploy each service with its own link and no tunnel.
Use test calls only there: Modal's GPUs are outside India, so real customer audio shouldn't go through them, and each
turn gains about 0.5-0.8 s of network time.

```powershell
pip install modal
python -m modal setup                     # log in; "modal" alone works only if pip's Scripts folder is on PATH
python -m modal secret create navya-speech SPEECH_SERVER_TOKEN=<secret> HF_TOKEN=<hugging-face-token>
python -m modal volume create navya-models
python -m modal volume put navya-models models/svara/svara-tts-v1-Q3_K_M.gguf svara/svara-tts-v1-Q3_K_M.gguf
python -m modal deploy deploy/modal_stt.py
python -m modal deploy deploy/modal_tts.py
```

- Accept the IndicConformer terms on Hugging Face before the first STT call; the model downloads into the
  `navya-models` volume then.
- GPU functions need a payment method on the Modal account, even while the free credits pay for the usage. Set a
  spending limit in Modal's billing settings. CPU functions (STT) deploy without one.
- The first TTS build compiles llama.cpp with CUDA (10-20 minutes); later deploys reuse it. The first start downloads
  Maya into the volume. TTS is ready when `/health` shows `"loaded": ["maya"]`.
- Generate each secret separately: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Only
  `SPEECH_SERVER_TOKEN` is shared, between Modal and the pipeline.

Keep one container of each running during a test session, then let them stop when idle so credits aren't spent:

```powershell
$env:NAVYA_MIN_CONTAINERS="1"; python -m modal deploy deploy/modal_tts.py; python -m modal deploy deploy/modal_stt.py
Remove-Item Env:NAVYA_MIN_CONTAINERS; python -m modal deploy deploy/modal_tts.py; python -m modal deploy deploy/modal_stt.py
```

Then point the pipeline's `.env` at the printed links and run it as in "Run it locally" and "Run the portal":

```ini
STT_SERVER_URL=wss://<workspace>--navya-stt-web.modal.run/v1/stt
TTS_PROVIDER=server
TTS_SERVER_URL=wss://<workspace>--navya-tts-web.modal.run/v1/tts
SPEECH_SERVER_TOKEN=<the same secret as on Modal>
```

`python -m speech.prerender --force` renders the cached lines through the TTS service, which also checks the link.
Until TTS runs somewhere, `TTS_PROVIDER=piper` or `TTS_PROVIDER=sarvam` keeps the rest of the pipeline usable.

Each container handles one utterance (STT) or one sentence (TTS) at a time, which suits a few test calls, not 10-20
calls at once. The local `.venv-tts` is only for `speech.local_tts_render` listening tests; the pipeline doesn't need it.

## Layout

```
agent/        dialogue engine, stages, prompts, tools, observer, knowledge snapshot, LiveKit worker
guardrails/   fast-path guard, backchannel filter, output guard, authorization context
speech/       normalizer, Hindi number words, lexicon, phrase cache, prerender, TTS clients, STT and TTS services
deploy/       Modal deploy files for the STT and TTS services
llm/          OpenAI-compatible streaming client (first-token timeout, failover, circuit breakers)
campaign/     control and portal API, login, minutes ledger, dialer and dial service, compliance, document processing
integrations/ payment links (Razorpay), WhatsApp/SMS (Plivo), CRM webhooks
database/     models (Postgres + pgvector), session, seed, Alembic migrations
observability/ metrics, cost calculator, call recorder, recording, tracing, logging
offline/      post-call summaries
frontend/     landing page with live demo, admin console, client portal, script console
scripts/      chat, create_admin, speech_check (test an STT or TTS link)
tests/        guards, normalizer, stages/tools/cost, engine (fake LLM), portal rules
```
