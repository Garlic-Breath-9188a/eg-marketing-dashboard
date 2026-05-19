# EG Marketing Dashboard

Independent Streamlit dashboard tracking Ezra Group marketing performance across HubSpot, GA4, and LinkedIn. Primary metric: **leads generated**, defined as HubSpot contacts whose `firm_type` is in the Ezra Group ICP set (RIA, Fintech, Bank/Trust, Custodian, Insurance, Broker-Dealer, Asset Manager).

## Status

**Phase 1 (in progress):** HubSpot contacts + lead classifier + unclassified-backlog metric, deployed behind a shared password.

See `STATUS.md` for the current session-handoff snapshot.

## Quickstart (local)

```bash
cd "EG Marketing Dashboard"
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit secrets.toml: HUBSPOT_TOKEN, DASHBOARD_PASSWORD
streamlit run app.py
```

## HubSpot token (Private App)

1. HubSpot → Settings → Integrations → Private Apps → Create a private app
2. Scopes (read-only):
   - `crm.objects.contacts.read`
   - `crm.objects.companies.read`
   - `crm.schemas.contacts.read`
   - `crm.schemas.companies.read`
   - `forms`
3. Copy the token into `.streamlit/secrets.toml`

## Lead definition

A contact is a **lead** if `Company.firm_type` (fallback: `Contact.firm_type`) is one of:

| Internal value (Company) | Label |
|---|---|
| `ria` | RIA |
| `fintech` | Fintech |
| `banktrust` | Bank/Trust |
| `custodian` | Custodian |
| `insurance` | Insurance |
| `brokerdealer` | Broker-Dealer |
| `asset_manager` | Asset Manager |

Contacts without a `firm_type` on either record are **unclassified** and surface in the Backlog page as cleanup work.

## Phasing

1. HubSpot contacts/forms + classifier + backlog (current)
2. GA4 — ezragroup.com + WealthTechToday.com
3. LinkedIn Company Page (Marketing API)
4. AuthoredUp CSV ingest for Craig's personal LinkedIn
5. HubSpot email campaign performance

## Hosting

Streamlit Community Cloud (free tier). Password gate via `st.secrets["DASHBOARD_PASSWORD"]`. Share the URL + password with team members and the fractional CMO.
