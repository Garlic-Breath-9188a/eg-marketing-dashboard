# STATUS — EG Marketing Dashboard

Last updated: 2026-06-14

## Where things live

| Asset | Location |
|---|---|
| GitHub repo (private) | `https://github.com/Garlic-Breath-9188a/eg-marketing-dashboard` |
| Deployed dashboard | `https://eg-marketing.streamlit.app` |
| Streamlit Cloud admin | [share.streamlit.io](https://share.streamlit.io) → My apps → `eg-marketing` |
| Local working copy (MacBook) | `Development Projects/EG Marketing Dashboard/` |

## What's deployed and working

### Data ingest (3 sources)
- **HubSpot Service Key** — contacts, companies, forms, submissions, deals, tasks
- **AuthoredUp API** — LinkedIn personal posts (Craig Iskowitz profile) + Ezra Group company page
- **WordPress REST API** — WealthTechToday.com posts (post metadata; Jetpack stats optional)

### Pages
- **Marketing Command Center** (`app.py`) — exception-based, deal-focused Overview (redesigned 2026-06-14). Top to bottom: header w/ Asana 90-Day Strategy link → **⚡ Do This Now** action queue → 4 Critical KPIs → **🔥 Hot Deals** → **🎯 Qualified Leads**. All secondary content (form spotlights, LinkedIn KPIs, source/week charts, operational counts, hygiene signals) was removed from the Overview and lives on the sub-pages.
- **Leads** (`pages/1_Leads.py`) — filterable lead list
- **Backlog** (`pages/2_Backlog.py`) — unclassified contacts to triage
- **Forms** (`pages/3_Forms.py`) — per-form drill-down with CSV export
- **LinkedIn** (`pages/4_LinkedIn.py`) — top posts by comments/engagement, posting cadence
- **Content** (`pages/5_Content.py`) — WordPress posts table + cadence chart

### Critical KPIs (current as of 2026-06-14)
- **Qualified leads** — RIA + Broker-Dealer + WealthTech vendors (Fintech), with a `X RIA · Y BD · Z WealthTech` breakdown
- **Open pipeline $** (+ open-deal count)
- **Closing this period** (count + $ at stake)
- **Tasks overdue** (+ tasks due in 7d)

### ⚡ Do This Now — exception-based action queue
A single ranked list of only the items needing action, each with a HubSpot/Asana deep link. Priority order:
1. 🚨 Overdue tasks → linked to associated deal
2. 🔥 Hot accounts to call (3+ contacts at one ICP firm engaged in 30d) → company
3. 💰 Open deals closing this period → deal
4. 📌 Tasks due in next 7 days
5. ⏰ Stalled leads (prior pipeline activity, 45+ days quiet) → contact
6. 💎 Multi-touch warm leads (3+ form fills, zero deals) → contact

Caps at 12 with a "+N more" note; shows "✅ You're clear" when nothing is urgent.

### 🔥 Hot Deals
Open deals ranked by a blended score: **value 0.5 · close-date proximity 0.3 · activity 0.2**. "Activity" = has an active HubSpot task overdue or due within 14 days. Columns: Deal, Company, Amount, Stage, Close date, Days open, Task-due flag, HubSpot link.

## What's NOT done / known gaps

- **Hot Accounts shows "No ICP firms" message** — needs more Company.firm_type classification in HubSpot to populate. Craig is enriching via HubSpot AI.
- **Pre-Feb 2026 data is suppressed** in period-over-period deltas (HubSpot adoption was Feb 2026). Adjustable via sidebar "Trust data from" date.
- **LinkedIn share / impression / reach metrics are NULL** from AuthoredUp — LinkedIn API restriction, not a bug. Reactions + comments + engagement rate are reliable.
- **Jetpack/WordPress.com Stats not connected** — Content page shows post metadata only. To add view counts: get `WPCOM_API_TOKEN` from [developer.wordpress.com/apps](https://developer.wordpress.com/apps).
- **Deals/tasks scopes confirmed working** (2026-06-13 refresh: 718 deals, 440 tasks). If tasks count is 0, the `crm.objects.tasks.read` scope may not be added yet.
- **Open-deals KPI** now derives "closed" from HubSpot pipeline-stage `isClosed` metadata (pulled at ingest), not hardcoded `closedwon`/`closedlost` literals — the portal uses custom numeric stage IDs across 3 pipelines, so the old literal filter counted every deal as open.

## Setting up on a new machine (Mac mini handoff)

```bash
# 1. Install Python 3.11+ if not present (macOS usually ships with python3)
python3 --version

# 2. Clone the repo
mkdir -p ~/dev
cd ~/dev
git clone https://github.com/Garlic-Breath-9188a/eg-marketing-dashboard.git
cd eg-marketing-dashboard

# 3. Create venv + install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Create local secrets file (DO NOT COMMIT — gitignored)
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Then edit .streamlit/secrets.toml in your editor and fill in:
#   HUBSPOT_TOKEN, AUTHOREDUP_API_KEY, DASHBOARD_PASSWORD,
#   WORDPRESS_BASE_URL, WORDPRESS_USER, WORDPRESS_APP_PASSWORD,
#   (optional) WPCOM_API_TOKEN, WPCOM_SITE
# Get these values from your password manager or the MacBook's secrets.toml.

# 5. Run locally
streamlit run app.py
# → http://localhost:8501
```

**Git config** — when you commit from the Mac mini, set author once:
```bash
git config user.email "craig@ezragroup.com"
git config user.name "Craig Iskowitz"
```

**GitHub auth** — first push from the new machine will prompt for a Personal Access Token. Reuse the existing PAT from Dashlane ("GitHub PAT - eg-marketing-dashboard"), or generate a new one at [github.com/settings/tokens](https://github.com/settings/tokens) with `repo` scope.

## Secrets needed in `.streamlit/secrets.toml`

(See `.streamlit/secrets.toml.example` in the repo for the full template with comments.)

| Key | Required? | Where to get |
|---|---|---|
| `HUBSPOT_TOKEN` | Yes | HubSpot → Settings → Integrations → Service Keys |
| `HUBSPOT_PORTAL_ID` | Optional | Makes record deep-links resolve instantly. The number in any HubSpot URL: `app.hubspot.com/contacts/{THIS}/…`. Auto-fetched at ingest if omitted. |
| `AUTHOREDUP_API_KEY` | Yes (for LinkedIn) | AuthoredUp web app → Settings → API Access |
| `DASHBOARD_PASSWORD` | Yes | You set it — what you share with viewers |
| `WORDPRESS_BASE_URL` | Yes (for Content tab) | `https://wealthtechtoday.com` |
| `WORDPRESS_USER` | Optional (read drafts) | Your WP username |
| `WORDPRESS_APP_PASSWORD` | Optional | WP admin → Users → Profile → Application Passwords |
| `WPCOM_API_TOKEN` | Optional (Jetpack views) | [developer.wordpress.com/apps](https://developer.wordpress.com/apps) |
| `WPCOM_SITE` | Required if using `WPCOM_API_TOKEN` | `wealthtechtoday.com` |

## HubSpot Service Key scopes currently in use

The Service Key must have ALL of these scopes (verify in HubSpot → Service Keys → edit):
- `crm.objects.contacts.read`
- `crm.objects.companies.read`
- `crm.schemas.contacts.read`
- `crm.schemas.companies.read`
- `forms`
- `marketing.campaigns.read`
- `crm.objects.deals.read`
- `crm.objects.owners.read`
- `crm.objects.tasks.read` ← verify this one is added (search "tasks" in scope picker)

If a scope is missing, the related ingest degrades gracefully (skips data) — no crash, but the corresponding KPIs show 0 / "—".

## Workflow for code changes from Mac mini

1. Pull latest: `git pull origin main`
2. Edit code (with Claude or directly)
3. Test locally: `streamlit run app.py`
4. Commit: `git add -A && git commit -m "msg"`
5. Push: `git push`
6. Streamlit Cloud auto-redeploys within ~30 seconds — refresh the dashboard tab

## Exclusion lists (non-prospect filters)

In `app.py`:
- `EXCLUDED_DOMAINS`: `ezragroup.com` (own), `grimmandco.org` (CMO), `streetcredpr.com` (PR), `mochadesigns.co` (dev partner)
- `EXCLUDED_EMAILS`: `grimm.marcus@gmail.com` (CMO personal)
- `ICP_FIRM_TYPES_FOR_OUTREACH`: `ria`, `brokerdealer`, `banktrust`, `custodian`, `asset_manager`, `fintech`

Edit these in `app.py` directly as new internal/vendor/partner contacts surface.

## Decision log

| Date | Decision | Notes |
|---|---|---|
| 2026-05-18 | Lead = `firm_type` in 7 ICP categories | Company.firm_type primary, Contact fallback |
| 2026-05-18 | Stack: Python + Streamlit + SQLite | Fastest to v1 |
| 2026-05-19 | LinkedIn = AuthoredUp API (not CSV) | Real API integration after Phase 4 plan changed |
| 2026-05-20 | Refocus dashboard to CRO/sales view | Hot Accounts + Pipeline Signals; classification noise demoted to expander |
| 2026-05-20 | Hot Accounts filter = 6 categories | RIA, Broker-Dealer, Bank/Trust, Custodian, Asset Manager, Fintech (omits Insurance from broader LEAD_CATEGORIES) |
| 2026-05-21 | Headline KPIs swapped to RIA / BD / Total leads / Open deals · tasks due / Tasks due | Form submissions + Total contacts moved to operational expander |
| 2026-05-21 | Deployed to Streamlit Cloud | `eg-marketing.streamlit.app` |
| 2026-05-21 | Added WordPress + Content tab | WealthTechToday integration |
| 2026-06-13 | Fixed open-deals KPI to use pipeline-stage `isClosed` metadata | Portal uses custom numeric stage IDs; old `closedwon`/`closedlost` literals matched nothing → all 718 deals shown as open (now 46 open / 672 closed) |
| 2026-06-13 | Pinned `requirements.txt` to verified majors | pandas 3.0 / streamlit 1.58 verified working; upper bounds guard against next-major drift |
| 2026-06-13 | Local venv must be built on Homebrew `python3.12` | Original venv's Python 3.12.0 framework was removed from the Mac, breaking it; system python3 is 3.9 (too old) |
| 2026-06-14 | Redesigned Overview → exception-based "Marketing Command Center" | Craig: too many fluff numbers. New layout: ⚡ Do This Now action queue → 4 critical KPIs → Hot Deals → Qualified Leads. Secondary content removed from Overview (kept on sub-pages). |
| 2026-06-14 | "Qualified leads" = RIA + Broker-Dealer + WealthTech (Fintech) | Craig's priority segments. WealthTech vendors = `fintech` firm_type. Drives the headline Qualified-leads KPI + the Qualified Leads table. |
| 2026-06-14 | Hot Deals score = value 0.5 · close-date 0.3 · activity 0.2 | Open deals only; activity = active task overdue/due within 14d. Replaces the old "deals are just a count" treatment. |
| 2026-06-14 | Added Asana link to 90-Day Marketing Strategy | Header button → `90-Day Marketing Demand Generation Plan` (project `1214458328037729`). Hardcoded `ASANA_STRATEGY_URL` in `app.py`. |
| 2026-06-14 | Closed deals: also trust the stage *label* | A custom-pipeline "Closed Lost" stage wasn't flagging `metadata.isClosed`, so Closed Lost deals (Dynasty, AssetMark) leaked into open deals / "closing this period." `_open_deals()` now ORs `stage_is_closed`, a "closed" substring in `stage_label`, and the legacy literal. |
| 2026-06-14 | HubSpot record links use canonical `/record/{type}/{id}` w/ portal ID | The `/contacts/_/company/{id}` shortcut didn't resolve. Now build `/contacts/{portalId}/record/0-1\|0-2\|0-3/{id}`. Portal ID from `HUBSPOT_PORTAL_ID` secret → cache meta (auto-fetched at ingest via `/account-info/v3/details`) → legacy `/_/` fallback. |
| 2026-06-14 | Task action links → Asana search by name | HubSpot tasks have no Asana ID, so overdue/due task rows link to `app.asana.com/0/search?q=<subject>` ("Edit in Asana ↗"). Craig manages tasks in Asana. |
| 2026-06-14 | Open/closed driven by per-deal `hs_is_closed`, not pipeline metadata | The `/crm/v3/pipelines/deals` isClosed approach left `stage_is_closed` NULL → all 718 deals counted as open. Now ingest pulls `hs_is_closed` / `hs_is_closed_won` (reliable computed props). Verified via HubSpot: **46 open / 671 closed**. Dashboard also falls back to known closed stage IDs so it's correct on pre-refresh caches. **Re-refresh after deploy to populate the new props.** |
| 2026-06-14 | Portal ID hardcoded default `50726076` | Confirmed from live HubSpot record URLs. Makes deep-links resolve without waiting on the `/account-info` fetch; still overridable via `HUBSPOT_PORTAL_ID` secret. |
| 2026-06-14 | Task links → HubSpot, NOT Asana (reverses earlier call) | The action-queue tasks are 440 real HubSpot sales tasks, not Asana items — searching Asana by name found nothing. Task rows now link to `/tasks/{portalId}/view/all/task/{id}` ("Open in HubSpot ↗"). The Asana header button (90-Day marketing *plan*) is unrelated and stays. |
| 2026-06-14 | Filter out HubSpot "(Sample task)" demo data | New portals ship sample tasks/contacts; "(Sample task) Follow up with Brian" etc. were polluting the overdue queue. `_active_tasks()` drops any subject starting with "(Sample task)". |
| 2026-06-14 | Prune deleted deals/tasks from cache on refresh | Refresh only upserted — deals/tasks deleted in HubSpot lingered forever as stale "overdue" items with dead links (e.g. a "full deck" task later replaced by "comprehensive deck"). Added `delete_deals_not_in` / `delete_tasks_not_in` with the same ≥50%-of-existing safety guard contacts use. **Requires a re-refresh to clear existing stale rows.** |
| 2026-06-14 | Hot-account action relabeled (was "Call X") | "Call Zocks" read like a task but is an account-to-contact prompt (3+ contacts engaged) linking to the company. Now "Hot account: X — reach out" with a "View company ↗" link. All action rows now state their link target (View company/deal/contact, Open in HubSpot). |

## Open items / next session ideas

1. **Connect Jetpack stats** to populate WordPress view counts on the Content tab
2. **HubSpot deep-link from company name** in Hot Accounts table (clickable)
3. **Email engagement in Hot Accounts heat score** (currently only counts form fills)
4. **LinkedIn → HubSpot crossover**: commenters on Craig's posts who are already HubSpot contacts (warmest possible leads — but requires matching LinkedIn URN to HubSpot contact, which is non-trivial)
5. **Owner names in deals/tasks views** (we have `hubspot_owner_id`; need a separate `/crm/v3/owners` pull to resolve to names)
6. **Scheduled refresh** — currently manual. Could add a daily refresh via a cron-style background job on the deployed instance.
