# Volopay Bill Approval Inbox — Architecture & README

A small, live web app that lets a finance team capture and approve vendor bills **through Claude**, using the Model Context Protocol (MCP). Claude does all the thinking (understanding plain English, extracting structured fields); the app does the saving, the approval workflow, and the live UI. **No AI runs inside the app** — there are no LLM API keys anywhere in this codebase.

**Live app:** https://volopay-bill-inbox.onrender.com
**MCP endpoint:** `https://volopay-bill-inbox.onrender.com/mcp/`

---

## 1. What it does (the one flow, done well)

In Claude, a finance user types:

> "Add a bill: AWS, $1,200, due Nov 30."

Claude calls the `create_bill` MCP tool with clean, structured arguments. The bill lands in the app's **Pending approval** queue. A human then reviews, edits, approves, marks paid, or rejects it in the web UI. Everything persists in a real database and stays live on the internet.

Three MCP tools, nothing more:

| Tool | What it does |
|---|---|
| `create_bill` | Add a vendor bill (starts as `pending`). |
| `list_bills` | List bills, optionally filtered by status. |
| `update_bill` | Change a bill's status or fields (approve, mark paid, reject, edit). |

---

## 2. Architecture

```
┌──────────────┐     plain English        ┌──────────────────────────┐
│   Finance    │  "Add a bill: AWS …"      │          Claude          │
│    user      │ ───────────────────────▶ │  (the reasoning engine)  │
└──────────────┘                           │  understands + extracts  │
       ▲                                   │  structured fields       │
       │ reviews / approves                └────────────┬─────────────┘
       │ in the browser                                 │ MCP tools/call
       │                                                │ (Streamable HTTP, HTTPS)
       │                                                ▼
┌──────┴───────────────────────────────────────────────────────────┐
│                 Render (public web service, HTTPS)                 │
│                                                                    │
│   FastAPI app ──mounts──▶ /mcp/  (MCP server: 3 tools)             │
│        │                        │                                  │
│        │ GET / , /bill/{id}/…   │ create_bill / list_bills /       │
│        │ (HTML approval UI)     │ update_bill                      │
│        └───────────┬────────────┘                                  │
│                    ▼                                                │
│             SQLModel / SQLAlchemy                                   │
└────────────────────┬───────────────────────────────────────────────┘
                     │ SQL over TLS
                     ▼
            ┌───────────────────┐
            │   Neon Postgres   │   ← permanent memory (survives restarts)
            └───────────────────┘
```

**Key point:** the same FastAPI process serves *both* the human web UI (`/`) and the Claude-facing MCP server (`/mcp/`). They read and write the **same database**, which is why a bill Claude creates appears instantly in the human's browser.

---

## 3. Data flow (step by step)

1. User types a request in Claude (e.g. "add a bill: AWS, $1,200, due Nov 30").
2. Claude parses the sentence, decides which tool to call, and fills structured arguments (`vendor="AWS"`, `amount=1200`, `due_date="2026-11-30"`).
3. Claude sends a `tools/call` request to `…/mcp/` over HTTPS (MCP Streamable HTTP transport).
4. The MCP server runs `create_bill`, inserts a `Bill` row (`status="pending"`, `source="claude"`) into Neon Postgres, and returns a human-readable confirmation plus the live review link.
5. The finance user opens the web app, sees the bill in the **Pending** queue, and acts on it (edit / approve / mark paid / reject) — plain FastAPI routes updating the same database.
6. Every state change is durable and visible to anyone with the link.

---

## 4. Responsibilities: Volopay (the product) vs. the AI partner (Claude)

A deliberate split, and the whole reason this is safe to ship:

| Concern | Owned by **the app (Volopay side)** | Owned by **Claude (AI partner)** |
|---|---|---|
| Understanding messy human language | — | ✅ |
| Extracting structured fields (vendor, amount, date) | — | ✅ |
| Deciding *which* action to take | — | ✅ |
| Storing data durably | ✅ | — |
| The approval workflow & statuses | ✅ | — |
| The system of record / audit trail | ✅ | — |
| Showing data to humans | ✅ | — |
| Final human approval of money decisions | ✅ (human in the loop) | — |

The app never calls an LLM. Claude never holds the data. This keeps the money-sensitive system of record fully under the company's control, while the AI stays a **capture-and-reasoning layer on top** — exactly where spend-management buyers will tolerate AI.

### Guardrails (the app decides what the AI is allowed to write)

Because AI can propose a financial record, the tools validate every call before anything is saved:

- **Amounts** must be a number greater than 0 (and an absurdly large amount is flagged, not silently accepted).
- **Due dates** must be a real ISO date (`YYYY-MM-DD`); relative phrases like "next Friday" are refused so Claude must resolve them first.
- **Vendor** cannot be blank; **status** must be one of `pending / approved / paid / rejected`.
- The **lifecycle is enforced server-side**: a bill can only move along valid transitions (pending→approved→paid, or →rejected→restored), and an approved or paid bill **cannot be hard-deleted** — it stays as an audit trail.

When a call fails a guardrail, the tool returns a clear, Claude-readable reason (e.g. *"Bill not created: amount must be greater than 0"*) instead of writing bad data — so Claude can correct itself in the conversation.

---

## 5. Tech stack & why

| Layer | Choice | Why |
|---|---|---|
| Web framework | **FastAPI + Starlette** | Async, tiny, serves HTML and mounts MCP in one process. |
| MCP server | **FastMCP** (`mcp` SDK, Streamable HTTP) | Official SDK; remote MCP that Claude connects to as a custom connector. |
| ORM / models | **SQLModel + SQLAlchemy** | One `Bill` model used by both web and MCP code. |
| Database | **Neon Postgres** (prod) / SQLite (local dev) | Neon = free, persistent, serverless Postgres. SQLite for zero-setup local runs. |
| DB driver | **psycopg v3** | Modern Postgres driver; `DATABASE_URL` is rewritten to `postgresql+psycopg://` at startup. |
| Hosting | **Render** (free web service) | Free public HTTPS URL; auto-deploys on every push to `main`. |
| Templates | **Jinja2** | Server-rendered HTML, no front-end build step. |

---

## 6. Assumptions

- A finance team already trusts Claude enough to let it capture (not approve) bills.
- Bills are low-to-mid complexity: vendor, amount, currency, due date, category, notes.
- A human makes every final approval decision; AI never moves money.
- The MCP endpoint is unauthenticated for this demo (see Limitations).
- Single team / single workspace — no multi-tenant separation yet.

---

## 7. Limitations (honest)

- **No authentication** on the MCP endpoint or the web UI. Anyone with the link can read/write. Fine for a demo; a real deployment needs auth (OAuth on the connector, SSO on the UI).
- **Free-tier cold start:** Render free sleeps after ~15 min idle, so the first request after idle takes ~30–60s to wake (then it's instant). The first MCP call may time out once and auto-retry. A `GET /health` endpoint is provided as a cheap keep-warm/liveness target; pointing a free uptime pinger at it (e.g. every 10 min) keeps the service awake during an evaluation window. On a paid Render instance this disappears entirely.
- **No real approval policy engine** — no spend limits, approver hierarchy, or duplicate detection yet.
- **No file/invoice attachment** — bills are structured fields only, no PDF upload or OCR.
- **No currency conversion** — currency is stored as a label, not normalized.
- **Single database, no backups configured** beyond Neon's defaults.

---

## 8. Next steps

1. **Auth:** OAuth on the MCP connector + SSO on the web UI, scoped per workspace.
2. **Policy engine:** spend limits, approver routing, duplicate-bill detection.
3. **Invoice ingestion:** let Claude read an attached PDF invoice and pre-fill the bill (OCR + extraction), human still approves.
4. **Multi-tenant:** workspaces, roles, per-user audit trail.
5. **Accounting sync:** push approved bills to the ledger / payment rails.
6. **Notifications:** ping an approver in Slack when a high-value bill lands.

---

## 9. Run it locally

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload        # web UI at http://localhost:8000
```

The MCP server is mounted at `http://localhost:8000/mcp/`. With no `DATABASE_URL` set, it uses a local SQLite file (`bills.db`). Set `DATABASE_URL` to a Neon/Postgres string for persistence, and `APP_BASE_URL` to the public URL so tool responses link correctly.

---

## 10. Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI web UI + mounts the MCP server. |
| `mcp_server.py` | The three MCP tools. No AI here. |
| `models.py` | The `Bill` data model. |
| `db.py` | Database connection (SQLite ↔ Postgres). |
| `templates/` | `base.html`, `index.html`, `edit.html` — the UI. |
| `render.yaml` | Render deploy config. |
| `requirements.txt` | Pinned dependencies. |
