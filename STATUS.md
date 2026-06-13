# STATUS — EG Marketing Dashboard

Last updated: 2026-06-13

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
- **Marketing Overview** (`app.py`) — Hot Accounts table, Pipeline Signals, CRO KPIs, charts, forms table, LinkedIn KPIs
- **Leads** (`pages/1_Leads.py`) — filterable lead list
- **Backlog** (`pages/2_Backlog.py`) — unclassified contacts to triage
- **Forms** (`pages/3_Forms.py`) — per-form drill-down with CSV export
- **LinkedIn** (`pages/4_LinkedIn.py`) — top posts by comments/engagement, posting cadence
- **Content** (`pages/5_Content.py`) — WordPress posts table + cadence chart

### Headline KPIs (current as of 2026-05-21)
- RIA leads (period)
- Broker-Dealer leads (period)
- Total leads (period)
- Open deals · tasks due 7d
- Tasks due 7d
- (Operational expander) Unclassified backlog, Total contacts, Non-ICP, Form submissions

### Pipeline Signals (deal-impact, CRO-grade)
- 🔥 Hot account (3+ contacts at one ICP firm engaged in 30d)
- ⏰ Stalled leads (had pipeline activity, 45+ days quiet)
- 💎 Multi-touch warm leads (3+ form fills, zero deals)
- 📉 Pipeline drought (rolling 4-week lead count down >30%)

Each signal has a dismiss checkbox; dismissals expire end-of-day.

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

## Open items / next session ideas

1. **Connect Jetpack stats** to populate WordPress view counts on the Content tab
2. **HubSpot deep-link from company name** in Hot Accounts table (clickable)
3. **Email engagement in Hot Accounts heat score** (currently only counts form fills)
4. **LinkedIn → HubSpot crossover**: commenters on Craig's posts who are already HubSpot contacts (warmest possible leads — but requires matching LinkedIn URN to HubSpot contact, which is non-trivial)
5. **Owner names in deals/tasks views** (we have `hubspot_owner_id`; need a separate `/crm/v3/owners` pull to resolve to names)
6. **Scheduled refresh** — currently manual. Could add a daily refresh via a cron-style background job on the deployed instance.
