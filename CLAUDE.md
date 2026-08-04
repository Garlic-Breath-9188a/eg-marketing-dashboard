# EG Marketing Dashboard — project instructions

Python + Streamlit + SQLite marketing command center. Orientation: **`STATUS.md`**
(current state, secrets, deploy) and **`MERGE_PLAN.md`** (in-progress merge work).
Deployed to Streamlit Cloud at `eg-marketing.streamlit.app`; a push to `main`
auto-redeploys within ~30s.

## Working style

- **Do it yourself — don't send Craig hunting for a button or menu.** If an action
  is within your reach (a shell/git command, an MCP tool, editing a file, forcing a
  redeploy with a commit), just do it. Do not give click-by-click UI instructions as
  the primary path.
- **Only hand a step back to Craig when you genuinely cannot do it** — blocked by a
  permission/classifier, requires his credentials or account/physical access you
  don't have, or it's a UI with no programmatic equivalent. When you must hand off,
  do everything you can first and reduce the leftover to one specific action.
- Concretely: prefer forcing a Streamlit reboot via a git commit/push over telling
  him to click "Reboot app"; prefer running a CLI/MCP call over describing where a
  setting lives.

## Deploy notes

- **Streamlit Cloud caches imported modules.** A plain code push reruns `app.py`
  from source but keeps `store/`, `ingest/`, `classify/` in memory — so newly-added
  functions there can `AttributeError` until the *process* restarts. An empty commit
  does NOT reliably force this. The reliable trigger is **changing `requirements.txt`**
  (forces an environment rebuild + restart). For hot-path features, prefer keeping
  the logic in `app.py` (always re-read fresh) over new module functions.
- `git push` is pre-authorized via `.claude/settings.local.json`.
