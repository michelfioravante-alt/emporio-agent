# Empório da Música — AI Support Agent

Prototype customer-support agent for the Artefact AI Engineer case.

The agent helps the fictional music store **Empório da Música** answer catalog, order and policy questions with a hybrid design:

- **SQL tools** for products, prices, stock, promotions and orders
- **Policy section retrieval** for the store manual
- **Gemini function calling** to decide which tool to use
- **FastAPI + simple chat UI** that shows tool calls for auditability

## Why this architecture

| Decision | Rationale |
|---|---|
| Hybrid tools + policy search | Structured facts (price/stock/order) must be exact. Policies are short enough for topic retrieval instead of a heavy vector RAG. |
| SQLite from CSVs | Tiny relational model, easy joins, deterministic filters. |
| Custom tool loop (no LangChain) | Fewer moving parts, easier to explain and debug. |
| Gemini Flash | Free tier, solid function calling, simple setup for reviewers. |
| FastAPI chat UI | Matches the full-stack flavor of the role while staying Python-only and reproducible. |
| Visible tool calls | Differentiator for reviewers: you can see whether the agent queried data or policies. |

## Setup

1. Python 3.11+ recommended.
2. Create a virtualenv and install dependencies:

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

3. Create `.env` from the example and add a free Gemini key from [Google AI Studio](https://aistudio.google.com/apikey):

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux
```

4. Run the app from this folder:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

5. Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

## API

- `GET /api/health` — public status (`ok` / `degraded`)
- `POST /api/chat` — `{ "message": "...", "history": [{ "role": "user|assistant", "content": "..." }], "include_debug": false }`

Customer responses return a friendly `activity` feed (what was consulted), not raw tool payloads.
For reviewers, open `http://127.0.0.1:8000/?review=1` to request a redacted `debug` summary (disabled when `APP_ENV=production`).

## Security posture

Production-minded defaults already in the prototype:

- Secrets only via `.env` (never committed)
- Host bound to `127.0.0.1` by default
- CORS allowlist (no `*`)
- Security headers + CSP
- OpenAPI docs disabled when `APP_ENV=production`
- Input size limits, history role allowlist, rate limiting
- Order lookup requires customer name or phone
- API errors are generic to clients; stack traces are not returned
- Tool traces exposed to the browser are redacted (no full PII dumps)
- Prompt hardened against jailbreak / secret exfiltration

## Data notes / assumptions

- CSVs are loaded into SQLite on startup.
- Only promotions with `is_active = 1` are offered.
- PIX 5% is documented as non-stackable with promotional prices.
- Order lookup requires `order_id` plus customer name or phone (LGPD-inspired).
- Phone numbers in the policy PDF disagree between cover and WhatsApp section; the agent exposes both as written instead of inventing a “fix”.
- Conversation language is Portuguese because that matches the store and the sample scenarios. This README stays in English for the selection process.

## Known limitations

- No real WhatsApp integration.
- Policy search is keyword/topic based, not embeddings.
- No long-term memory beyond the browser session history sent to the API.
- Gemini quality depends on the configured model and key quota.

## Using AI coding assistants

This project was built with **Cursor**. Workflow:

1. Read the case PDF and profile the CSVs.
2. Decide architecture together (hybrid tools, FastAPI UI, Gemini).
3. Implement modules iteratively: DB → tools → agent loop → UI.
4. Manually review tool contracts, privacy rules and README claims.

## Example conversations

See `examples/` after the demo transcripts are added.

## Project layout

```text
app/
  agent.py      # Gemini tool-calling loop
  tools.py      # SQL + policy tools
  db.py         # CSV → SQLite
  policies.py   # PDF section retrieval
  prompts.py    # Persona and hard rules
  main.py       # FastAPI app
static/         # Chat UI
data/           # Challenge CSVs + policy PDF
examples/       # Sample transcripts
```
