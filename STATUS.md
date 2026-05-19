# STATUS — EG Marketing Dashboard

Session: 2026-05-18

## What's built (Phase 1 — code complete, untested)

- Repo scaffolded at sibling level (`Development Projects/EG Marketing Dashboard/`)
- HubSpot ingest: paginated contact + company pulls into local SQLite cache, with token from `st.secrets`
- Lead classifier: Company.firm_type (primary) → Contact.firm_type (fallback) → 7 ICP categories (RIA, Fintech, Bank/Trust, Custodian, Insurance, Broker-Dealer, Asset Manager)
- Streamlit app with password gate
  - **Overview** (`app.py`): KPI cards (leads / total / unclassified / non-ICP), leads-by-week bar chart, leads-by-source bar chart
  - **Leads** (`pages/1_Leads.py`): filterable contact-level list of classified leads
  - **Backlog** (`pages/2_Backlog.py`): unclassified contacts with conversion events, deep-linked to HubSpot for cleanup

## What's NOT done

- **Hasn't run yet.** No local Python env created, no `streamlit run` smoke test. Next session needs to install deps + run locally to validate.
- **No HubSpot token configured.** Craig needs to create a Private App in HubSpot with read scopes (see README.md) and put the token in `.streamlit/secrets.toml`.
- **Not deployed.** Streamlit Community Cloud setup is Task #6, deferred until local run works.
- **Phases 2–5 not started:** GA4, LinkedIn Company Page, AuthoredUp CSV ingest, HubSpot email campaigns.

## Open data hygiene issue (HubSpot)

`Contact.firm_type` is in a worse state than `Company.firm_type`:
- Duplicates: both "Fintech" and "Fintech Vendor" exist as separate values
- Stray value "Invite team members" looks like a UI accident
- Missing categories: TAMP, Robo-Advisor, Hedge Fund, Press/PR
- Uses display labels (mixed case w/ slashes) as internal values instead of normalized slugs

**Recommendation:** Schedule a separate HubSpot-admin cleanup task. Don't bundle into dashboard work.

## Next steps (in order)

1. Create HubSpot Private App and grab the token
2. Local `pip install -r requirements.txt`, `streamlit run app.py`, sign in with password, click "Refresh from HubSpot"
3. Validate the lead counts against a sanity-check query in HubSpot ("how many contacts created in last 30d with Company.firm_type in our 7 categories?")
4. Push to GitHub, deploy to Streamlit Community Cloud
5. Share URL + password with fractional CMO

## Decisions log

| Date | Decision | Notes |
|---|---|---|
| 2026-05-18 | Lead = `firm_type` in 7 ICP categories | Craig spec'd; uses Company.firm_type as truth, Contact as fallback |
| 2026-05-18 | LinkedIn personal = AuthoredUp CSV drop | Craig confirmed OAuth tool, weekly export workflow |
| 2026-05-18 | Stack = Python/Streamlit + SQLite cache | Fastest to v1, native password gate |
| 2026-05-18 | Hosting = Streamlit Community Cloud | Free, supports `st.secrets` for password + tokens |
| 2026-05-18 | Sibling repo, separate from Enforcement Monitor | Independence requirement; only shared substrate is HubSpot portal |
