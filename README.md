# Inbound AI Voice Call Centre

Production-grade AI **inbound** voice call centre for **TextileProjects.in** — callers dial in, an AI Investment Assistant (Priya) qualifies them, captures lead data to CSV and books expert consultations on Cal.com.

- **Telephony:** Vobiz SIP trunk (inbound dial-in)
- **Voice AI:** Google **Gemini Live** real-time
- **Orchestration:** LiveKit Agents 1.x
- **Backend:** FastAPI + APScheduler
- **DB:** Supabase (PostgreSQL) + per-call lead CSV
- **Booking:** Cal.com consultant scheduling
- **Dashboard:** Single-file HTML + Chart.js
- **Deployment:** Docker, Coolify-ready

---

## Call flow

1. Caller dials the Vobiz number → LiveKit inbound trunk → agent answers with `Greeting.wav`.
2. AI asks for the caller's name, then a 5-option **qualification menu** (first business / expansion / exploring / project reports / machinery & consultants).
3. Scenario-specific questions (investment budget, current business, …) + 3 **lead-qualification** questions.
4. Every answer is saved instantly to the **lead CSV** via the `save_lead_info` tool.
5. **Consultation pitch** → mobile + email captured → expert session booked on **Cal.com**.

---

## 🔒 Configuration policy — single source of truth

**Real environment variables on the host (VPS / Coolify) are the ONLY source of truth for credentials and infra config.** No `.env` file or DB row can override them at runtime.

| Where it lives | What lives there |
| --- | --- |
| **VPS env vars** | All credentials, `LIVEKIT_*`, `GOOGLE_API_KEY`, `GEMINI_*`, `VOBIZ_*`, `INBOUND_TRUNK_ID`, `DEFAULT_TRANSFER_NUMBER`, `SUPABASE_*`, `TWILIO_*`, `S3_*`, `CALCOM_*`, `LEAD_CSV_PATH` |
| **Supabase `settings` table** | Only `system_prompt` and `ENABLED_TOOLS` — UI-editable text. Nothing else. |
| **Supabase tables** (`appointments`, `call_logs`, `agent_profiles`, …) | Application data |
| **Lead CSV** (`LEAD_CSV_PATH`, default `leads.csv`) | Caller answers: qualification choice, budget, product interest, timeline, mobile, email |
| **`.env` file** | Optional, **local dev only**. Loaded only when `OUTBOUNDAI_LOAD_DOTENV=true` is set, and even then with `override=False`. |

Implications:

- The Settings tab in the dashboard is **read-only diagnostics** for credentials. To change an API key, update it in Coolify and redeploy.
- After clicking ⚡ Create SIP Trunk, the dashboard shows the new trunk ID; you must paste it into `INBOUND_TRUNK_ID` in Coolify and redeploy.

---

## Required API keys

| Key | Where to get it | Needed for |
| --- | --- | --- |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | cloud.livekit.io → Project → Keys | Call orchestration (required) |
| `GOOGLE_API_KEY` | aistudio.google.com/app/apikey | Gemini Live voice AI (required) |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `SUPABASE_ANON_KEY` | supabase.com → Project Settings → API | Call logs, profiles (required) |
| `INBOUND_TRUNK_ID` | Dashboard ⚡ Create SIP Trunk or `create_trunk.py` | Receiving calls (required) |
| `VOBIZ_SIP_DOMAIN` / `VOBIZ_USERNAME` / `VOBIZ_PASSWORD` / `VOBIZ_INBOUND_NUMBER` | vobiz.ai | Phone number (required) |
| `CALCOM_API_KEY` / `CALCOM_EVENT_TYPE_ID` | cal.com → Settings → Developer → API Keys | Consultant booking |
| `LEAD_CSV_PATH` | you choose (e.g. `/data/leads.csv`) | Persistent lead capture |
| `DEFAULT_TRANSFER_NUMBER` | your call-centre number | Human transfer |
| `TWILIO_*`, `S3_*`, `DEEPGRAM_API_KEY` | twilio.com / any S3 / deepgram.com | Optional: SMS, recordings, fallback STT |

See `.env.example` for the full annotated list.

---

## File map

```
agent.py                  LiveKit worker — Gemini Live inbound entrypoint
server.py                 FastAPI backend
db.py                     Supabase async DB + env-only policy
tools.py                  10 LLM function tools (incl. save_lead_info CSV capture)
prompts.py                TextileProjects.in qualification flow prompt
Greeting.wav              Pre-recorded greeting played on answer
start.sh                  Production startup (bash, traps, fail-fast)
Dockerfile                Python 3.11-slim + HEALTHCHECK
.dockerignore             Keeps .env, .git, leads.csv out of the image
requirements.txt          Python deps
supabase_schema.sql       Run once in Supabase SQL Editor
.env.example              Template for local dev only
ui/index.html             Single-file dashboard
```

---

## VPS deployment via Coolify

1. **Supabase** → SQL Editor → run `supabase_schema.sql`.
2. Push this repo to GitHub.
3. **Coolify** → New Resource → Application → connect the repo. Coolify auto-detects the `Dockerfile`.
4. **Build pack:** Dockerfile.
5. **Port:** `8000`. Coolify will reverse-proxy it as HTTPS automatically.
6. **Environment Variables:** paste every variable from the section above.
7. **Persistent storage:** mount a volume (e.g. `/data`) and set `LEAD_CSV_PATH=/data/leads.csv` so lead data survives redeploys.
8. **Health check:** Coolify uses the Dockerfile `HEALTHCHECK` automatically (`GET /healthz`).
9. Deploy.
10. After the first deploy, open the dashboard:
    - `Settings` → verify every credential shows `✓ set in VPS env`.
    - `Settings → ⚡ Create SIP Trunk` → copy the returned trunk ID into Coolify as `INBOUND_TRUNK_ID` → redeploy.
    - Call your Vobiz number to verify end-to-end audio and the greeting.

### How processes run inside the container

- `start.sh` (PID 1, bash) traps `SIGTERM`, validates required env vars, then starts:
  - **uvicorn** on `0.0.0.0:8000` (FastAPI dashboard + REST API)
  - **`python agent.py start`** — LiveKit worker (outbound websocket to LiveKit Cloud; inbound calls arrive through LiveKit, no extra port needed)
- `wait -n` ensures **either** child crashing kills the container so Coolify restarts it.
- The `HEALTHCHECK` curls `http://127.0.0.1:8000/healthz` every 30 s.

### Network requirements

The container needs egress to:

- LiveKit Cloud (`*.livekit.cloud`, port 443/wss)
- Google Generative AI (`generativelanguage.googleapis.com`, 443)
- Supabase (`*.supabase.co`, 443)
- Cal.com (`api.cal.com`, 443)
- Vobiz SIP (RTP/SIP via LiveKit's network — handled by LiveKit Cloud, not directly by this container)

**No inbound port** required other than 8000 for the dashboard.

---

## Local development

```bash
cp .env.example .env
# fill in the keys

export OUTBOUNDAI_LOAD_DOTENV=true
pip install -r requirements.txt
bash start.sh
```

`OUTBOUNDAI_LOAD_DOTENV=true` is the **only** way to make the app read the `.env` file. Without it, `.env` is ignored — exactly as it will be in production.

---

## Smoke test after deploy

```bash
# Replace with your Coolify URL
APP=https://inbound.example.com

# 1. Health
curl -fsS $APP/healthz                               # → {"status":"ok"}

# 2. Settings show env vars are configured
curl -fsS $APP/api/settings | head -c 500

# 3. Call your Vobiz inbound number from a phone — you should hear the
#    greeting, then the AI qualification flow. Afterwards check the lead CSV:
cat /data/leads.csv    # (inside the container / mounted volume)
```

If any of those fail, open `Logs` in the dashboard for the agent / server stack traces.

---

## Critical runtime rules (do not deviate)

| Rule | Why |
| --- | --- |
| Never `close_on_disconnect=True` with SIP | SIP audio dropouts kill the session |
| All 3 silence-prevention configs (session resumption + window compression + tuned end-sensitivity) | Without them, calls go silent in 30–90 s |
| Gemini 3.1 opener uses `session.say()` — never fall back to `generate_reply()` for the first turn | Gemini 3.1 rejects `send_client_content`, causing the timeout from livekit/agents#5260 |
| Server on port 8000, agent worker outbound-websocket only | Hardcoded in `start.sh` |
| Never bake credentials into the image, `.env`, or Supabase | Single source of truth = host env vars |
| `leads.csv` stays gitignored & dockerignored | Caller PII never enters the repo or image |

---

## Cost (per minute, India)

| Service | ₹/min |
| --- | --- |
| Vobiz SIP | 1.00 |
| LiveKit Cloud | 0.17 |
| Gemini Live | 0.03 |
| **Total** | **≈ 1.20** |
