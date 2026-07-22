# Merge Plan — Sales Task Dashboard → EG Marketing Dashboard

Created: 2026-07-22

## Goal

Collapse two projects (and two orphaned background services) into **one Streamlit
app**: the existing marketing dashboard becomes the single Ezra Group command
center, with the unified task list and the daily briefing as pages inside it.
Stop all Slack *posting*. Keep Slack *reading* (mentions are a task source).

## Decisions taken

| Decision | Choice |
|---|---|
| Roland's data fetchers | Port to Python as Streamlit ingest modules; retire `server.js` |
| Fathom meeting-intelligence agent | Keep as a separate local launchd service (dashboard reads its output from Asana/HubSpot) |
| Daily briefing | Becomes the dashboard home page — live, not a once-a-day Slack snapshot |
| Home repo | `eg-marketing-dashboard` (has git + Streamlit Cloud deploy; Sales Task Dashboard has neither) |
| Outlook drafts | **Off, both systems.** Craig rarely used them and two were being generated per call |
| Replacement | Track every prospect call and flag the ones with no follow-up email sent |
| "Followed up" = | Any sent email to any external attendee after the call time (new thread or reply) |
| Overdue after | 2 business days — green <2d, amber 2–4d, red >4d |

## Prospect follow-up tracker

Replaces draft generation. The dashboard records every prospect call and watches
Sent Items to confirm a follow-up actually went out.

**This runs entirely inside Streamlit** — Fathom API, Anthropic API and MS Graph
are all reachable from Streamlit Cloud, so the tracker needs no local service.

- `ingest/fathom.py` — pull meetings from `GET /meetings` (`x-api-key` auth,
  response key `items`, cursor pagination), classify prospect vs not, store.
  Reuse the Zapier v37 classifier prompt — it is the tuned definition of
  "prospect" (includes vendor demos, referral partners, BD conversations) and
  has 30 documented test calls behind it in the Fathom Monitor notes.
- `store/db.py` — new `prospect_calls` table: `recording_id` (PK), `call_title`,
  `call_at`, `company`, `attendees_json` (external only, with emails),
  `fathom_url`, `followed_up_at`, `follow_up_subject`, `follow_up_to`.
- `ingest/outlook.py` — add a Sent Items scan:
  `GET /me/mailFolders/SentItems/messages`, `$select` `sentDateTime`,
  `toRecipients`, `subject`, `webLink`. For each open prospect call, match any
  recipient against the call's external attendee emails with
  `sentDateTime > call_at`. First match wins and stamps `followed_up_at`.
- `pages/7_Follow_ups.py` — "Awaiting follow-up" ranked oldest-first with the
  RAG threshold above, plus a "Sent" section for the audit trail. Each row links
  to the Fathom recording and the HubSpot company.
- Surface the red count on the briefing home page so it can't be missed.

### Deliberate design notes

- **Sent-Items matching is by attendee email, not thread.** Craig replying inside
  an existing thread still counts, which is the common real-world case.
- **Only external attendees count.** `@ezragroup.com` / `@ezragroupllc.com` are
  filtered out, matching the existing Zapier HubSpot-deal-check logic.
- **Backfill on first run.** Seed from the last 90 days of Fathom meetings so the
  tracker opens with real history rather than an empty table.

## Current state (verified 2026-07-22)

Four systems, not two:

1. **EG Marketing Dashboard** — Python/Streamlit + SQLite, git repo, deployed to
   `eg-marketing.streamlit.app`. Ingests HubSpot (contacts, companies, forms,
   deals, tasks), AuthoredUp, WordPress.
2. **Sales Task Dashboard / "Roland AI"** — Node/Express, 2,325 lines in one
   `server.js`, **no git repo**. launchd `com.ezragroup.roland-dashboard` →
   `localhost:3001` (PID confirmed running). Two unrelated jobs in one file:
   task aggregation, and the Fathom agent.
3. **Paperclip COS agent** — launchd `com.ezragroup.paperclip` → `localhost:3100`.
   Fetches `/api/briefing`, writes the narrative, posts the "Ezra Group — Daily
   Briefing" to Craig's Slack self-DM (`D7RM1GR29`) as Craig. **This is what
   posts to Slack — not either dashboard.** Last briefing: 2026-07-15.
4. **Zapier Zap 273528709** — separate Fathom pipeline. Classifies prospect
   calls, drafts a follow-up email in Outlook, files the transcript to the
   SharePoint Team Site, posts to `#sales`.

### Known duplication to resolve

Roland's Fathom agent and the Zapier Zap **both create an Outlook draft** for
prospect calls. Currently dormant only because Roland's MS Graph refresh token
expired (`AADSTS700082`). Re-authenticating without deduping first would produce
two drafts per prospect call. Decide which one owns email drafting before
restoring the token.

## Already done (no porting needed)

`store/db.py` already has `deals` and `tasks` tables with full HubSpot ingest
(`ingest/hubspot.py`), and `app.py` already renders an exception-based
"⚡ Do This Now" queue over them. The port is only the sources Streamlit
doesn't have yet.

## Work plan

### Phase 0 — Safety net ✅ done 2026-07-22

- [x] `git init` in `Sales Task Dashboard/` — snapshot commit `15aea56`
      (6,996 lines, `.env` excluded via new `.gitignore`).
- [x] Outlook drafting removed from the Fathom agent — commit `aca02a7`.
      MS Graph scope narrowed to `Mail.Read`. Service restarted and verified
      (`/api/status` 200, 276 tasks).
- [x] Zapier Zap 273528709 — Outlook draft step deleted, published as **v40**.
      Both draft generators are now off; zero drafts per prospect call.
- [ ] Re-authenticate MS Graph — the refresh token expired 2026-07-01
      (`AADSTS700082`). Follow-up detection cannot work until this is done.
- [ ] Copy `.env` values into `.streamlit/secrets.toml` keys (Asana, Slack,
      MS Graph). Do not commit.

### Phase 1 — Port the task sources to Python

New modules under `ingest/`, mirroring the existing `hubspot.py` shape
(`refresh()` → upsert into SQLite):

- [x] `ingest/asana.py` — done 2026-07-22, commit `56ab96e`. **185 tasks**
      (the Node version silently capped at 100 — 28 overdue tasks were hidden).
      Also fixed company mis-tagging; see `classify/companies.py`.
- [ ] `ingest/slack.py` — port `fetchSlackItems()` (server.js:589).
      Read-only: `search.messages` for mentions. **Do not port
      `chat.postMessage`.**
- [ ] `ingest/outlook.py` — port `refreshMsToken()` + `fetchOutlookEmails()`
      (server.js:691, 733)
- [ ] `store/db.py` — add `unified_tasks` table spanning all sources
      (id, source, source_id, name, project/company, due_date, priority, url,
      amount, completed) + upserts and prune-not-in guards matching the
      existing `delete_deals_not_in` pattern.

### Phase 2 — Port the logic layer

- [ ] `classify/priority.py` — port `computeSmartPriority()` (server.js:1403).
      Pure function, direct translation, worth unit tests.
- [ ] `classify/companies.py` — port the `COMPANY_ALIASES` matching that tags
      Asana tasks and Slack messages with a HubSpot company (server.js:1470-1510).

### Phase 3 — New pages

- [ ] `pages/6_Tasks.py` — the unified task list: all sources, priority-sorted,
      filterable by source/company/tier. Replaces `localhost:3001/dashboard`.
- [ ] Rework `app.py` home into the **Daily Briefing** page — the sections from
      the Slack DM: outbound touches, overdue, due today, campaign status,
      HubSpot campaign stats, weighted pipeline, top priority today. Fold the
      existing "⚡ Do This Now" queue into it rather than keeping both.
- [ ] Task completion write-back (Asana `PUT`, HubSpot `PATCH`) — port from
      `/api/complete/:id` (server.js:1700), **minus** the Slack thread reply.

### Phase 3b — Prospect follow-up tracker

- [ ] `ingest/fathom.py` + `prospect_calls` table + 90-day backfill
- [ ] Sent Items scan in `ingest/outlook.py`
- [ ] `pages/7_Follow_ups.py` + red count on the briefing page

### Phase 4 — Split out the Fathom agent

- [ ] New sibling project `Fathom Agent/` containing only the meeting-intelligence
      half of `server.js` (lines ~793-1400: poll, classify, Claude analysis,
      Asana/HubSpot task creation) plus `agents/meeting-intelligence/instructions/`
      and `.fathom-processed.json`. Own git repo.
- [ ] Point launchd `com.ezragroup.roland-dashboard` at it, or relabel to
      `com.ezragroup.fathom-agent`. **If the folder is renamed, update
      `WorkingDirectory` in the plist in the same step** — a stale path here
      caused the 6-day silent crash-loop in July.
- [ ] Resolve the duplicate-Outlook-draft overlap with the Zapier Zap.

### Phase 5 — Decommission

- [ ] Unload + remove `com.ezragroup.paperclip` (this is what stops the Slack
      briefing). Confirm nothing else depends on Paperclip first.
- [ ] Retire the Express server: `/dashboard`, `/cos`, `/editor`, `/api/*`.
- [ ] Zapier Zap 273528709 Step 16 (Slack `#sales` post) — Craig's call whether
      that one goes too; transcript filing to SharePoint should stay either way.
- [ ] Archive `Sales Task Dashboard/` once the Fathom agent is extracted.
- [ ] Update `STATUS.md` in the merged repo; delete the old one.

## Zapier Zap 273528709 — ✅ done 2026-07-22 (published as v40)

**Only step 3 (Microsoft Outlook — Create Draft Email) was deleted.** Step 2
(Claude — Send Message) was **kept**, and that turned out to matter: a
dependency scan showed **step 4 (Formatter) reads its input from step 2**, so
deleting step 2 as originally planned would have broken the chain that feeds the
Evernote note. Step 3's output was referenced by nothing.

Resulting step order (was 6+, now one fewer):

| Was | Now | Step |
|---|---|---|
| 1 | 1 | Fathom — New AI Summary (trigger) |
| 2 | 2 | Anthropic (Claude) — Send Message |
| **3** | — | **Microsoft Outlook — Create Draft Email (deleted)** |
| 4 | 3 | Formatter — Text |
| 5 | 4 | Code by Zapier — Run Python |
| 6 | 5 | Anthropic (Claude) — Send Message |

Note the live Zap was already at **v39** ("fix sycophantic AI voice, no
flattery"), one version newer than the v38 recorded in the Fathom Monitor notes —
those notes are behind.

**To revert:** Zapier → Versions → republish v39. There is also a Copilot
checkpoint on the v40 edit.

### How this was done (for next time)

Zapier MCP **cannot** edit Zap definitions — it only calls actions inside
connected apps. Zap editing is browser-only. The working path:

1. Drive `zapier.com/editor/<id>` with the claude-in-chrome browser tools.
2. The editor canvas does **not** render in screenshots and does **not** respond
   to synthetic clicks — three delete attempts on the step menu did nothing.
3. What worked: type the instruction into the Zap editor's own **Copilot** panel.
   Text input registers normally. Copilot made the edit; `get_page_text` was then
   used to verify the resulting step list independently.

Step 16 still posts to `#sales`. That's a separate decision from the drafts —
see Phase 5.

## Risks / open items

- **Streamlit Cloud has no persistent disk** — SQLite is rebuilt on redeploy.
  Already true today (refresh is manual), but the task list will feel staler than
  a 5-minute-cache API did. May want a scheduled refresh (STATUS.md open item #6).
- **MS Graph refresh token is expired** and Streamlit Cloud can't host the
  `/auth/callback` OAuth redirect. Re-auth locally, then paste the refresh token
  into Streamlit secrets.
- **Outlook client secret expires 2026-09-27** (per the March COS setup notes).
- **Briefing loses its historical narrative.** The Slack version tracked
  week-over-week outbound totals and campaign status across days. A live page
  reads current state. If the trend matters, it needs a table to persist daily
  snapshots — otherwise that history ends when Paperclip stops.
