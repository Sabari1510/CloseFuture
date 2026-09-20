# SETUP.md — Accounts, Keys and Environment Setup (Beginner Guide)

Backend only, no frontend files. Companion to `CLAUDE.md` and `AGENT.md`.
Dashboards change their button names now and then; if a label differs slightly, look for the closest match.

---

## 0. What you need, and when

You do **not** need everything on day one.

| Needed for | Setup step | Cost |
|---|---|---|
| Phase 0 (foundations) | §1 Tools, §2 Supabase | Free |
| Phase 1 (knowledge base, Search) | §3 LLM + embeddings key | From your test-key credit (~$2) |
| Phase 5 (Scheduler) | §4 Google Calendar | Free |
| Phase 6 (Lead-Summary) | §5 Email | Free tier |
| Every phase | §6 `.env` file | — |

Suggested order: §1 → §2 → §3 → §6 → (later) §4 → §5.

---

## 1. Tools on your computer (~15 min)

- **Python 3.11+** (`python --version`)
- **Git**
- **Docker Desktop** (optional but recommended: a free local test database, so tests cost nothing)
- **A code editor** (VS Code) and, if you want help writing code, an **AI coding assistant** of your choice (Cursor, GitHub Copilot, an AI chat, etc.). None is required

Local test database (optional):
```bash
docker run -d --name pgtest -e POSTGRES_PASSWORD=postgres -p 5433:5432 pgvector/pgvector:pg16
# local URL: postgresql://postgres:postgres@localhost:5433/postgres
```

---

## 2. Supabase (database + pgvector) (~15 min)

1. Go to **supabase.com** → sign up (GitHub or email) → **New project**.
2. Fill in: project name, a **database password** (use letters and numbers only, so the URL doesn't break; **save it**), and the **region closest to you and your visitors**. Choose the Free plan.
3. Wait 1–2 minutes for the project to be ready.
4. **Enable extensions:** open **SQL Editor → New query**, paste, and run:
   ```sql
   create extension if not exists vector;
   create extension if not exists btree_gist;
   ```
   (`pg_cron` / `pg_net` can be enabled later under **Database → Extensions** if you want scheduled jobs. Not needed for the demo.)
5. **Get the connection string:** click **Connect** at the top of the project page → choose **Session pooler** → copy the URI.
   ```
   postgresql://postgres.<project-ref>:<YOUR-PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```
   Replace `[YOUR-PASSWORD]` with your real password.
6. Put it in `.env` as `DATABASE_URL`.

**Which connection string?**
| Option | Use it? |
|---|---|
| **Session pooler (port 5432)** | **Yes**, works on IPv4 and IPv6 networks. Best default |
| Direct connection | Only if your network supports IPv6 (many home connections don't) |
| Transaction pooler (port 6543) | No: doesn't support prepared statements, which causes errors with our database driver |

**You do not need the Supabase `anon` / `service_role` API keys** for this backend. It talks to Postgres directly through `DATABASE_URL`. Never put database credentials in browser code.

**Free-plan note:** free projects pause after about a week of inactivity. Before a demo, open the dashboard and restore it if paused.

---

## 3. LLM + embeddings key (~10 min)

You need two capabilities: a **chat model** (small and cheap) and an **embedding model** (for the vector store). Not every provider offers both, so choose one path:

| Path | Chat | Embeddings | Keys |
|---|---|---|---|
| **A (simplest): one provider** (e.g. OpenAI or Google Gemini) | ✔ | ✔ | 1 key |
| B: Anthropic for chat + another provider for embeddings (Anthropic doesn't offer its own embedding model; it points to Voyage AI) | ✔ | separate account | 2 keys |

**Steps (any provider):**
1. Create an account on the provider's developer console (e.g. platform.openai.com, console.anthropic.com, or aistudio.google.com).
2. Add a **small prepaid balance or billing** if required.
3. **Set a hard monthly spend limit** in the billing settings. This key is for testing and has only about $2 of credit, so the limit is your safety net.
4. Create an **API key** and copy it immediately (it's shown once). Put it in `.env`.
5. Pick the provider's **cheapest small chat model** for `LLM_MODEL_SMALL`, and note the embedding model and its **dimension**.
   - Example: OpenAI `text-embedding-3-small` → `EMBED_DIM=1536`.
   - `EMBED_DIM` must match the `vector(<EMBED_DIM>)` column in the database.
6. Copy the current per-million-token prices into `config/pricing.yaml` (the cost ledger uses it).

---

## 4. Google Calendar (~30 min)

**Why not just an "API key"?** A plain API key only reads *public* data. Creating events on your calendar needs **OAuth**: you approve once, and we save a **refresh token** so the backend can act as the calendar owner later. Meet links and visitor invites also work this way.

### 4.1 Create the project and enable the API
1. Open **console.cloud.google.com** → top bar → **New project** → name it (e.g. `lead-bot`) → Create.
2. **APIs & Services → Library** → search **Google Calendar API** → **Enable**.

### 4.2 Consent screen
3. Open **Google Auth Platform** (older name: OAuth consent screen) → **Get started**.
4. App name (e.g. `Lead Bot`), your email as support email → **Audience: External** → contact email → finish.
5. **Audience → Test users → Add users** → add the Google account whose calendar will be used. **Only listed test users can approve the app.**

### 4.3 Create the OAuth client
6. **Clients → Create client → Application type: Desktop app** → name it → Create.
7. Copy the **Client ID** and **Client secret** (or download the JSON) into `.env`.

### 4.4 Get the refresh token (one time)
8. In Phase 5, you (or your coding assistant) create `scripts/google_auth.py`. Run it:
   ```bash
   python scripts/google_auth.py
   ```
   A browser opens → sign in as the calendar owner → if you see **"Google hasn't verified this app"**, click **Advanced → Continue** (it's your own app) → allow access.
9. The script prints a **refresh token**. Save it as `GOOGLE_REFRESH_TOKEN` in `.env`.
   - Script requirements (give these to whoever writes it): request offline access and force the consent prompt (otherwise Google may not return a refresh token); use the scope `https://www.googleapis.com/auth/calendar` for simplicity (narrow it later if you wish).

### 4.5 Good to know
- **7-day expiry:** while the app's publishing status is **Testing** (External), refresh tokens expire after **7 days**. Re-run `google_auth.py` if you see `invalid_grant`, and **do it the day before your demo**.
- Changing your Google password can revoke tokens that include Gmail scopes.
- Use a **dedicated test calendar or account** while developing.
- Set `GOOGLE_CALENDAR_ID=primary` (or a specific calendar's ID from its settings), and `CALENDAR_TZ` to the calendar owner's IANA time zone (e.g. `Asia/Kolkata`).

---

## 5. Email for the lead summary (~10 min)

The visitor's **calendar invite** is sent by Google Calendar. This step is only for the **lead-summary email to your sales team**.

### Option A (default): Resend
1. Sign up at **resend.com**.
2. **API Keys → Create API Key** (sending access) → copy the key (`re_...`) into `.env`.
3. **Testing without a domain:** use `EMAIL_FROM=onboarding@resend.dev`. On the free plan, this sandbox sender can deliver **only to the email address you signed up with**. So set `SALES_INBOX` to your own signup address for the demo. Sending to another address returns a 403.
4. **Going live:** add your domain under **Domains**, add the DNS records (SPF/DKIM) they show, wait for "verified", then use `EMAIL_FROM=leads@yourdomain.com` and any `SALES_INBOX`.
5. Free tier is roughly 100 emails/day, 3,000/month. Plenty for this project.
6. Resend supports **idempotency keys**, which fits our "never send duplicates" rule (FR-6.6).

### Option B: Gmail API (no domain needed, can send to any address)
1. In the same Google Cloud project: **Library → Gmail API → Enable**.
2. Add the scope `https://www.googleapis.com/auth/gmail.send` to your auth script and **re-run it** to get a new refresh token covering both Calendar and Gmail.
3. Emails are sent **from your own Gmail account**. Threaded updates are supported natively.

Pick **one** option and tell whoever is writing the code which (`EMAIL_PROVIDER=resend` or `gmail`).

---

## 6. The `.env` file

Create `.env` in the project root (copy from `.env.example`). **Never commit it.** Confirm `.gitignore` contains `.env` and `*client_secret*.json`.

```bash
# --- LLM / embeddings ---
LLM_PROVIDER=openai                # openai | anthropic | google (as you chose)
LLM_API_KEY=
LLM_MODEL_SMALL=                   # cheapest small chat model of your provider
EMBED_MODEL=text-embedding-3-small
EMBED_DIM=1536
EMBED_API_KEY=                     # only if embeddings use a different provider

# --- Database (Supabase, Session pooler URI) ---
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/postgres

# --- Google Calendar ---
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
GOOGLE_CALENDAR_ID=primary
CALENDAR_TZ=Asia/Kolkata           # calendar owner's time zone

# --- Email ---
EMAIL_PROVIDER=resend              # resend | gmail
RESEND_API_KEY=
EMAIL_FROM=onboarding@resend.dev
SALES_INBOX=                       # your own signup email while on the Resend sandbox

# --- App ---
COMPANY_NAME=
ADMIN_API_KEY=                     # generate below
ALLOWED_ORIGINS=http://localhost:3000
BUDGET_TOTAL_USD=2.00
BUDGET_SOFT_USD=1.20
BUDGET_HARD_USD=1.70
IDLE_ABANDON_MINUTES=2             # demo value; use 20 in production
```

Generate the admin key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Secret hygiene**
- Keys live only in `.env` (local) or your host's secret manager (deployment).
- If a key is ever pasted in chat, a screenshot or Git, **revoke it and create a new one immediately**.
- Never share the Google client secret, refresh token, database password or `ADMIN_API_KEY`.

---

## 7. Verify everything (build this in Phase 0)

Build a `make check-setup` command (yourself or with an assistant) that tests each item and prints pass/fail (no LLM cost beyond one tiny call):

| Check | Pass means |
|---|---|
| Database | Connects with `DATABASE_URL`; `vector` and `btree_gist` extensions present |
| LLM | One tiny chat call succeeds; tokens recorded in the cost ledger |
| Embeddings | One embedding call returns a vector of length `EMBED_DIM` |
| Google | Lists your next calendar events using the refresh token |
| Email | Sends a test email to `SALES_INBOX` |
| Config | All required `.env` variables present; admin key set |

**Prompt to paste into your AI coding assistant (if you use one):**
> Read CLAUDE.md, AGENT.md and SETUP.md. Do Phase 0 only. Include a `make check-setup` command that verifies database, pgvector, LLM, embeddings, Google Calendar and email using the `.env` variable names in SETUP.md §6. Skip any check whose credentials are not set yet and report it as "not configured". Do not create any frontend files.

---

## 8. Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| Database connection times out | Network is IPv4-only → use the **Session pooler** string, not Direct |
| "prepared statement already exists" errors | You used the **Transaction pooler (6543)** → switch to Session pooler (5432) |
| Password rejected / URL parse error | Special characters in the password → reset it under Database settings, use letters and numbers only |
| `extension "vector" is not available` | Run the `create extension` SQL in §2 step 4 |
| Supabase says project paused | Restore it from the dashboard (free-plan inactivity pause) |
| Google "Access blocked / not verified" | Add your account under **Audience → Test users** |
| Google `redirect_uri_mismatch` | Client type must be **Desktop app** |
| Google `invalid_grant` | Refresh token expired or revoked (7-day Testing limit, or password change) → re-run `google_auth.py` |
| No refresh token printed | Auth flow needs offline access + forced consent; revoke the app at myaccount.google.com → Security → Third-party access, then re-run |
| Event created but no Meet link | The Calendar insert must request conference data creation (`conferenceDataVersion=1`); tell your assistant |
| Visitor got no invite | Attendee must be set and update emails enabled (`sendUpdates="all"`) |
| Resend 403 "can only send testing emails to your own address" | Sandbox rule → set `SALES_INBOX` to your signup email, or verify a domain |
| Embedding insert fails (dimension mismatch) | `EMBED_DIM` doesn't match the embedding model → fix env and migration |
| LLM 401 / 429 | Wrong key / billing not active / rate limit → check key, billing and spend limit |

---

## 9. Setup checklist

- [ ] Python 3.11+, Git, Docker (optional), editor, editor and any coding assistant installed
- [ ] Supabase project created; DB password saved; `vector` + `btree_gist` enabled
- [ ] Session-pooler `DATABASE_URL` in `.env`
- [ ] LLM account with **hard spend limit** set; API key in `.env`; embedding model + `EMBED_DIM` chosen
- [ ] Google project, Calendar API enabled, consent screen (External), your account added as **test user**
- [ ] Desktop OAuth client created; ID + secret in `.env`
- [ ] Refresh token obtained and saved (repeat within 7 days if still in Testing)
- [ ] Email option chosen (Resend or Gmail); key/sender/`SALES_INBOX` in `.env`
- [ ] `ADMIN_API_KEY` generated
- [ ] `.env` and `*client_secret*.json` are in `.gitignore`
- [ ] `make check-setup` passes
