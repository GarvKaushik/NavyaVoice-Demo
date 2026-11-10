# Setup and running guide

Everything needed to run this project from a fresh clone: the pipeline, the STT and TTS services (on your machine,
on someone else's machine through a link, or on Modal), the website, and real phone calls through Twilio or Plivo.
Commands are for Windows PowerShell unless marked otherwise.

## 1. What runs where

```
                 ┌────────────── Pipeline (this machine) ──────────────┐
 Browser ──────► │ Website (frontend/)  ─►  API (campaign.api)         │
 Phone  ◄─SIP──► │ LiveKit (+ SIP)      ◄►  Call worker (agent.worker) │──► LLM (OpenAI or self-hosted)
                 │ Postgres, Redis           Dialer (campaign.background)
                 └───────────┬─────────────────────────┬───────────────┘
                             │ wss://…/v1/stt          │ wss://…/v1/tts
                      STT service                TTS service
                 (CPU is enough)            (NVIDIA GPU for Maya)
             local · another machine · Modal
```

STT and TTS are separate services. The pipeline only needs their links and a shared token in `.env`, so each can run
on this machine, on a teammate's machine, or on Modal, and move without code changes.

## 2. What you need

| Need | For |
| --- | --- |
| Python 3.11 or 3.12 (3.10-3.12 on a TTS machine) | Everything in Python |
| Node.js 18+ | The website |
| Docker Desktop | Postgres, Redis and LiveKit for development |
| Git | Getting the code |
| An OpenAI API key (or a self-hosted LLM) | The agent's replies |
| A Hugging Face account and read token | Downloading the STT model (IndicConformer) |
| An ngrok account, or cloudflared | Only to share a service running on a laptop |
| A Modal account | Only to run STT/TTS on Modal (GPU needs a payment method) |
| A Twilio or Plivo account, and LiveKit Cloud or a cloud VM | Only for real phone calls |

## 3. Get the code and install

```powershell
git clone <repo url>
cd <repo folder>
python -m venv .venv
.venv\Scripts\activate                       # Linux/WSL: source .venv/bin/activate
pip install -r requirements.txt -r requirements-local.txt
cd frontend; npm install; cd ..
copy .env.example .env                       # Linux/WSL: cp .env.example .env
```



## 4. Configure `.env`

`.env` holds your keys. It is ignored by Git: never commit it, and share the project through Git (push and clone),
not as a zipped folder, which would include it.

Make each secret separately (run once per secret):

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Setting | Value |
| --- | --- |
| `ENVIRONMENT` | `development` |
| `AUTH_SECRET` | a generated secret (signs website logins) |
| `LLM_PROVIDER`, `OPENAI_API_KEY` | `openai` and your key; or `gemini` with `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`); or `selfhosted` with the `SELFHOSTED_LLM_*` settings. Switching is this one line, then restart the worker and API |
| `SPEECH_SERVER_TOKEN` | a generated secret, the **same value** on the pipeline and on every STT/TTS service |
| `HF_TOKEN` | your Hugging Face read token (only where STT runs) |
| `STT_PROVIDER` | `indicconformer` |
| `STT_SERVER_URL` | the STT link (section 6) |
| `TTS_PROVIDER` | `server` for the TTS service; `piper` (local, robotic) or `sarvam` (Bulbul, paid) as stand-ins |
| `TTS_SERVER_URL` | the TTS link (section 6) |
| `TTS_SERVER_VOICE` | `maya-ahana`; cached audio is filed under this name, so change it when the voice changes |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | `ws://localhost:7880`, `devkey`, `secret` for local development |

Every other setting has a working default; `.env.example` describes them.

## 5. Start the development services and the database (once)

```powershell
docker compose up -d postgres redis livekit
python -m alembic upgrade head
python -m database.seed_data                       # demo client, campaign, products and script
python -m scripts.create_admin you@example.com     # your admin login for the website
```

## 6. STT and TTS: pick where they run

Whichever you pick, check a link before using it:

```powershell
python -m scripts.speech_check stt --url <STT link>                 # speak for 5 s, prints the transcript
python -m scripts.speech_check stt --url <STT link> --text "Price kya hai?"
python -m scripts.speech_check tts --url <TTS link>                 # writes tts_check.wav: listen to it
```

`403` means a wrong or missing token; `404` means nothing is at that link (the server or tunnel is down, or the path
is missing).

### 6a. On Garv's machine (simplest)

```powershell
python -m speech.stt_server --port 8765 --log-level info            # own terminal
python -m speech.tts_server --port 8766 --log-level info            # own terminal; needs a GPU for Maya
```

```ini
STT_SERVER_URL=ws://127.0.0.1:8765/v1/stt
TTS_SERVER_URL=ws://127.0.0.1:8766/v1/tts
```

The first STT request downloads and loads IndicConformer (about 20 s after that, under 1 s per reply). Without a GPU,
use `TTS_PROVIDER=piper` in the pipeline instead of a TTS service.

### 6b. On another machine, shared by a link

The host machine does sections 3 and 4 (with the pipeline owner's `SPEECH_SERVER_TOKEN`), then:

**STT host** (any laptop, CPU is enough): accept the terms of `ai4bharat/indic-conformer-600m-multilingual` on Hugging
Face, set `HF_TOKEN`, then

```powershell
python -m speech.stt_server --port 8765 --log-level info
```

**TTS host** (Linux, or Windows with WSL2 Ubuntu; NVIDIA GPU with about 10 GB or more):

```bash
pip install -r requirements-tts-server.txt --extra-index-url https://download.pytorch.org/whl/cu128
CMAKE_ARGS="-DGGML_CUDA=on" pip install --force-reinstall --no-deps --no-cache-dir llama-cpp-python
# Svara fallback voice: copy svara-tts-v1-Q3_K_M.gguf into models/svara/ (ask the project owner),
# or set TTS_FALLBACK_ENGINE= in .env to run Maya alone
python -m speech.tts_server --port 8766 --log-level info
```

The first TTS start downloads Maya (about 5 GB); it is ready when http://127.0.0.1:8766/health shows
`"loaded": ["maya"]`. Without a GPU, `TTS_ENGINE=piper` and `TTS_FALLBACK_ENGINE=` give a robotic CPU voice that is
enough to test a link.

**Make the link** (one tunnel per service, in its own terminal):

```powershell
winget install ngrok.ngrok                             # Linux/WSL: https://ngrok.com/download
ngrok config add-authtoken <your ngrok authtoken>      # once; keep it private
ngrok http 8765  (copy the link )                             # STT   
ngrok http 8766  (copy the link)                              #TTS
```

If PowerShell says `ngrok` isn't recognised, open a new terminal, or run
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe`.

ngrok prints `Forwarding https://<name>.ngrok-free.dev -> http://localhost:8765`. Replace `https://` with `wss://`
and add the path:

| Service | Link |
| --- | --- |
| STT | `wss://<name>.ngrok-free.dev/v1/stt` |
| TTS | `wss://<name>.ngrok-free.dev/v1/tts` |

The link changes whenever ngrok restarts. ngrok's free fixed domain keeps it the same:
`ngrok http --url=<your-domain>.ngrok-free.dev 8765`. Without an ngrok account:
`cloudflared tunnel --url http://localhost:8765` gives `https://<words>.trycloudflare.com` (use `wss://…/v1/stt`).

The host checks the link with `scripts.speech_check`, then sends it to the pipeline owner. The token is never sent
with the link. Keep the service and tunnel terminals open and the machine awake while it is in use; Ctrl+C in the
tunnel terminal cuts access. Logs: the service terminal shows each connection (`[accepted]` or `403`) and each
utterance; http://127.0.0.1:4040 lists every request through ngrok.

### 6c. On Modal

```powershell
pip install modal
python -m modal setup                       # "modal" alone works only if pip's Scripts folder is on PATH
python -m modal secret create navya-speech SPEECH_SERVER_TOKEN=<token> HF_TOKEN=<hugging-face-token>
python -m modal volume create navya-models
python -m modal volume put navya-models models/svara/svara-tts-v1-Q3_K_M.gguf svara/svara-tts-v1-Q3_K_M.gguf
python -m modal deploy deploy/modal_stt.py  # CPU; works on the free plan
python -m modal deploy deploy/modal_tts.py  # T4 GPU; needs a payment method on the Modal account
```

Each deploy prints a link like `https://<workspace>--navya-stt-web.<region>.modal.run`. Use
`wss://<that host>/v1/stt` and `wss://<that host>/v1/tts`. Health: `https://<that host>/health`.

- The first TTS build compiles llama.cpp with CUDA (10-20 minutes); later deploys reuse it.
- Keep one container warm during a test session, then turn it off so credits aren't spent while idle:
  ```powershell
  $env:NAVYA_MIN_CONTAINERS="1"; python -m modal deploy deploy/modal_stt.py
  Remove-Item Env:NAVYA_MIN_CONTAINERS; python -m modal deploy deploy/modal_stt.py
  ```
- Stop an app: `python -m modal app stop navya-stt` (or `navya-tts`). Start it again with `python -m modal deploy …`;
  the link stays the same.
- To copy the token and Hugging Face token from `.env` into the Modal secret without showing them:
  ```powershell
  python -c "import subprocess, sys; from config.settings import get_settings as g; s = g(); subprocess.run([sys.executable, '-m', 'modal', 'secret', 'create', 'navya-speech', f'SPEECH_SERVER_TOKEN={s.SPEECH_SERVER_TOKEN}', f'HF_TOKEN={s.HF_TOKEN}', '--force'], check=True)"
  ```
- Modal runs outside India: use it for test calls only, and expect about 0.5-0.8 s more per turn.

### Using a link in the pipeline

```ini
STT_SERVER_URL=wss://<STT host>/v1/stt
TTS_PROVIDER=server
TTS_SERVER_URL=wss://<TTS host>/v1/tts
SPEECH_SERVER_TOKEN=<the shared token>
```

Then check both links, start the pipeline (section 7), and render the cached lines in the voice:

```powershell
python -m speech.prerender --force
```

### Integrating the STT service into another project

Clients connect to `wss://<host>/v1/stt` with the header `Authorization: Bearer <token>` (with ngrok, also
`ngrok-skip-browser-warning: true`), then send:

1. `{"type": "config", "engine": "indicconformer", "sample_rate": 8000}` as JSON text
2. the audio as binary frames: raw 16-bit little-endian mono PCM at that rate
3. `{"type": "flush"}` when the speaker stops

The server replies `{"type": "final", "text": "..."}` after each flush. It sends nothing when the connection opens
and never sends transcripts on its own; a client that waits for a greeting, or never sends `flush`, hangs. The full
message format is at the top of `speech/stt_server/app.py`.

## 7. Run the pipeline

Each in its own terminal, with the virtual environment active:

```powershell
python -m uvicorn campaign.api:app --port 8000     # API
python -m agent.worker start                       # call worker (also serves browser and demo calls)
python -m campaign.background                      # dialer, summaries, CRM webhooks (needed for campaigns)
cd frontend; npm run dev                           # website at http://localhost:5173
```

Sign in as the admin, add a plan and a client, set the client's products and script, and create a login for them.

## 8. Try it

| What | How |
| --- | --- |
| Talk to the agent with your mic, no website | `python -m agent.worker console` |
| Talk to the agent as a website visitor (no login) | http://localhost:5173 -> Talk to Priya |
| Browser test call as a client | Website -> Test & call -> In this browser |
| Real phone call | Website -> Test & call -> On a phone (needs section 9) |
| Text chat with the LLM | `python -m scripts.chat` |
| Automated tests | `python -m pytest` |

The landing demo is on by default (`DEMO_CALL_ENABLED`), lasts up to `DEMO_MAX_SECONDS`, and is limited to
`DEMO_CALLS_PER_HOUR` per visitor and `DEMO_MAX_CONCURRENT` at once. Each demo call uses the real LLM and TTS.

## 9. Real phone calls

The pipeline dials through a SIP trunk in LiveKit, so the phone provider is only configuration. Until
`LIVEKIT_SIP_OUTBOUND_TRUNK_ID` is set, the website says phone calling isn't set up yet.

### 9a. LiveKit that the phone network can reach (needed for either provider)

Phone audio travels over UDP, which ngrok and cloudflared can't carry, so LiveKit can't stay on a laptop behind a
home router for phone calls.

- **LiveKit Cloud (simplest):** create a project at livekit.io and copy its URL, API key and secret. The worker and API
  stay on your machine and point at it.
- **Or a cloud VM** with a public IP running LiveKit server and LiveKit SIP, with SIP port 5060 and the media port
  range open. `docker-compose.yml` and `k8s/` have starting points.

```ini
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=<key>
LIVEKIT_API_SECRET=<secret>
```

Install the LiveKit CLI (`winget install LiveKit.LiveKitCLI`) and sign it in to the project (`lk cloud auth`, or pass
`--url`, `--api-key` and `--api-secret`). The trunk is created with a JSON file; keep it outside the repo, since it
holds the provider password, and delete it afterwards:

```json
{
  "trunk": {
    "name": "Outbound",
    "address": "<provider termination address>",
    "numbers": ["<your provider number, +E.164>"],
    "authUsername": "<trunk username>",
    "authPassword": "<trunk password>"
  }
}
```

```powershell
lk sip outbound create outbound-trunk.json      # prints the trunk ID, ST_...
```

```ini
LIVEKIT_SIP_OUTBOUND_TRUNK_ID=ST_...
```

`livekit_sip.json` in the repo is a placeholder for a future inbound setup, and `scripts/provision_livekit_sip.py`
(mentioned in settings) doesn't exist yet; use the steps above.

### 9b. Twilio (quickest for a demo)

1. Create a Twilio account and **verify your own mobile number**: trial accounts can only call verified numbers.
2. Get a Twilio phone number (the trial includes one, usually a US number).
3. Voice -> Settings -> **Geo Permissions**: enable **India**.
4. Elastic SIP Trunking -> create a trunk:
   - **Termination:** set a termination address (`<name>.pstn.twilio.com`) and add a **credential list** (username
     and password).
   - **Numbers:** attach your Twilio number.
5. Create the LiveKit trunk (9a) with `address` = `<name>.pstn.twilio.com`, `numbers` = your Twilio number, and the
   credential list's username and password.

Limits: the trial plays "this is a trial account" first, which the agent's answering-machine check may take for a
machine and hang up (upgrading Twilio removes it). A US caller ID may show oddly in India. Twilio isn't suitable for
promotional calls to real leads in India.

### 9c. Plivo (the route for real clients in India)

1. Sign up at plivo.com (needs a company email) and complete **KYC** for India; approval can take days.
2. Add your own mobile as a verified number while on the trial.
3. Rent a Plivo number to call from. Promotional calls to real leads need a registered **140-series** number and DLT
   registration under TRAI rules; a test call to your own phone doesn't.
4. Zentrunk -> **Outbound Trunks** -> create a trunk, add a **credentials list** (username and password), and note
   the termination address (`<id>.zt.plivo.com`).
5. `.env`:
   ```ini
   PLIVO_ZENTRUNK_TERMINATION_URI=<id>.zt.plivo.com
   PLIVO_ZENTRUNK_USERNAME=<username>
   PLIVO_ZENTRUNK_PASSWORD=<password>
   PLIVO_CALLER_ID_PROMOTIONAL=+91<your Plivo number>
   ```
6. Create the LiveKit trunk (9a) with `address` = `<id>.zt.plivo.com`, `numbers` = your Plivo number, and the
   Zentrunk username and password.

WhatsApp and SMS messages (payment links) also go through Plivo; they stay sandboxed (logged, not sent) in
development until its messaging settings are filled in.

### 9d. Place the call

1. Admin console -> the client -> set its **Number** to your Twilio or Plivo number. Without it, development uses
   `PLIVO_CALLER_ID_PROMOTIONAL`, which the provider will refuse if it isn't yours.
2. The client needs minutes (admin console -> add minutes, or approve a credit request).
3. Run the pipeline (section 7), sign in as the client -> **Test & call -> On a phone** -> your verified number ->
   **Call now**, and answer. The result, transcript and summary appear in **Call results**.

Calls are only placed from 9:00 to 20:00 in the lead's time zone (`COMPLIANCE_CALLING_*_HOUR`), and never to numbers on
the client's opt-out list or the national DND list.

## 10. When something goes wrong

| Problem | Fix |
| --- | --- |
| `modal` or `ngrok` "is not recognized" | Use `python -m modal …`; for ngrok see 6b, or open a new terminal |
| `403` from a speech service | `SPEECH_SERVER_TOKEN` differs between the pipeline and the service; a service reads `.env` only when it starts, so restart it after changing the token |
| `404` from an ngrok or Modal link | The server or tunnel isn't running, the app is stopped, or the link lacks `/v1/stt` or `/v1/tts`; ngrok links end in `.ngrok-free.dev` |
| Connects, then no reply | The client never sends `{"type": "flush"}`, sends audio as text, or waits for a greeting (6, "Integrating") |
| First reply takes ~20 s | The model is loading after a start; later replies are fast |
| Modal: "Please add a payment method to use T4 GPU functions" | Add a card on Modal (credits are used first) or host TTS elsewhere (6b) |
| Modal TTS build fails in llama-cpp-python | Check `python -m modal image logs <image id>`; the deploy file sets the compiler and GPU targets |
| Website says phone calling isn't set up yet | Section 9 |
| "Not called: outside calling window" | Call between 9:00 and 20:00 |
| "You are out of minutes" | Add minutes to the client in the admin console |
| Tests fail after editing `.env` | Run `python -m pytest`; tests don't depend on `.env`, so report the failing test |

## 11. Costs and stopping things

- **Modal:** idle apps cost nothing unless `NAVYA_MIN_CONTAINERS` was set; `python -m modal app stop <app>` stops one.
- **ngrok / cloudflared:** Ctrl+C ends the link at once.
- **The landing demo and phone calls** use the real LLM, TTS and phone minutes; `DEMO_CALL_ENABLED=false` turns the
  demo off.
- **Docker services:** `docker compose down` (add `-v` only to delete the database).
