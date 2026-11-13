"""LinkedIn page — post performance from AuthoredUp."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from store import db

if not st.session_state.get("authed"):
    st.warning("Sign in on the main page first.")
    st.stop()


@st.cache_data(ttl=3600)
def load_posts() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        df = pd.read_sql("SELECT * FROM linkedin_posts", conn)
    if "published_at" in df.columns:
        df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True)
    return df


@st.cache_data(ttl=3600)
def load_actors() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        return pd.read_sql("SELECT * FROM linkedin_actors", conn)


st.title("💼 LinkedIn Performance")
st.caption("Post performance from AuthoredUp. Comments are the highest-signal engagement metric for B2B outreach.")

posts = load_posts()
actors = load_actors()

if posts.empty:
    st.warning(
        "No LinkedIn data yet. Click **🔄 Refresh from AuthoredUp** in the sidebar of the Overview page."
    )
    st.stop()

# ---- Actor scope (drives the whole page) ----
today = datetime.now(timezone.utc).date()
default_start = today - timedelta(days=90)

actor_options = ["All actors"] + actors["name"].fillna("(unknown)").tolist()
selected_actor = st.selectbox("Actor", options=actor_options, index=0)
if selected_actor != "All actors":
    actor_id = actors[actors["name"] == selected_actor]["id"].iloc[0]
    actor_posts = posts[posts["actor_id"] == actor_id].copy()
else:
    actor_posts = posts.copy()


# ===========================================================================
# Recommended actions + trailing-window comparisons (30 / 60 / 90 / 365 days)
# ===========================================================================
def _col_sum(df: pd.DataFrame, col: str) -> float:
    return float(df[col].fillna(0).sum()) if col in df.columns else 0.0


def _wstats(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Aggregate the key metrics over [start, end)."""
    w = df[(df["published_at"] >= start) & (df["published_at"] < end)]
    react, comm, shar = _col_sum(w, "reaction_count"), _col_sum(w, "comment_count"), _col_sum(w, "share_count")
    rate = w["engagement_rate"].dropna() if "engagement_rate" in w.columns else pd.Series(dtype=float)
    return {
        "Posts": float(len(w)),
        "Impressions": _col_sum(w, "impression_count"),
        "Comments": comm,
        "Reactions": react,
        "Shares": shar,
        "Engagement": react + comm + shar,
        "Followers gained": _col_sum(w, "followers_gained_count"),
        "Avg eng. rate": float(rate.mean()) if not rate.empty else 0.0,
    }


_now = pd.Timestamp.now(tz="UTC")
_day = pd.Timedelta(days=1)
# Current = last 30 days. Each prior window is the 30-day block ending that many
# days ago, so deltas read as "this month vs the month that ended 30/60/90/365d ago".
cur = _wstats(actor_posts, _now - 30 * _day, _now)
prior = {
    "vs 30d prior": _wstats(actor_posts, _now - 60 * _day, _now - 30 * _day),
    "vs 60d prior": _wstats(actor_posts, _now - 90 * _day, _now - 60 * _day),
    "vs 90d prior": _wstats(actor_posts, _now - 120 * _day, _now - 90 * _day),
    "vs 1y prior": _wstats(actor_posts, _now - 395 * _day, _now - 365 * _day),
}
p30 = prior["vs 30d prior"]


def _pct(cur_v: float, prior_v: float) -> str:
    if prior_v == 0:
        return "—" if cur_v == 0 else "new"
    return f"{(cur_v - prior_v) / prior_v * 100:+.0f}%"


def _fmt(metric: str, value: float) -> str:
    if metric == "Avg eng. rate":
        return f"{value:.2%}" if value < 1 else f"{value:.1f}%"
    return f"{value:,.0f}"


# ---- Recommended actions (top of page) ----
st.subheader("✅ Recommended actions")
_scope = "across all actors" if selected_actor == "All actors" else f"for **{selected_actor}**"
_actions: list[str] = []
if cur["Posts"] == 0:
    _actions.append(
        f"🔴 **No posts in the last 30 days** {_scope}. Publishing has stopped — get back on a "
        "cadence; visibility and inbound both decay fast without it."
    )
elif cur["Posts"] < p30["Posts"]:
    _actions.append(
        f"🟠 **Posting slowed** — {cur['Posts']:.0f} posts in the last 30 days vs "
        f"{p30['Posts']:.0f} the month before. Aim to at least match last month's cadence."
    )
if p30["Comments"] > 0 and cur["Comments"] < p30["Comments"]:
    _actions.append(
        f"🟠 **Comments down {_pct(cur['Comments'], p30['Comments'])}** vs the prior 30 days. Comments are "
        "the highest-signal B2B metric — end posts with a direct question and reply to every comment."
    )
elif cur["Comments"] > p30["Comments"] and p30["Comments"] > 0:
    _actions.append(
        f"🟢 **Comments up {_pct(cur['Comments'], p30['Comments'])}** vs the prior 30 days — the current "
        "format is landing. Keep doing more of it."
    )
if p30["Impressions"] > 0 and cur["Impressions"] < p30["Impressions"] * 0.8:
    _actions.append(
        f"🟠 **Reach fell {_pct(cur['Impressions'], p30['Impressions'])}** vs last month. Try posting at your "
        "best-performing time and leading with a stronger hook."
    )
# Point at the single best recent post to replicate.
_recent = actor_posts[actor_posts["published_at"] >= _now - 30 * _day]
if not _recent.empty and "comment_count" in _recent.columns and _recent["comment_count"].fillna(0).max() > 0:
    _best = _recent.loc[_recent["comment_count"].fillna(0).idxmax()]
    _snippet = str(_best.get("text") or "").strip().replace("\n", " ")[:110]
    _actions.append(
        f"🟢 **Replicate your best recent post** ({int(_best['comment_count'])} comments): “{_snippet}…”"
    )
if not _actions:
    _actions.append(
        "🟢 **Cadence and engagement are steady or up vs last month.** Keep publishing on schedule "
        "and keep replying to comments."
    )
for _a in _actions[:5]:
    st.markdown(f"- {_a}")

# ---- KPI comparison: last 30 days vs 30 / 60 / 90 days & 1 year prior ----
st.subheader("📈 Last 30 days vs prior periods")
_mc = st.columns(5)
for _col, _m in zip(_mc, ["Posts", "Impressions", "Comments", "Engagement", "Followers gained"]):
    _delta = None if p30[_m] == 0 else f"{(cur[_m] - p30[_m]) / p30[_m] * 100:+.0f}% vs 30d ago"
    _col.metric(f"{_m} (30d)", _fmt(_m, cur[_m]), _delta, delta_color="off" if _m == "Followers gained" else "normal")

_comp_rows = []
for _m in ["Posts", "Impressions", "Comments", "Reactions", "Engagement", "Followers gained", "Avg eng. rate"]:
    _row = {"Metric": _m, "Last 30d": _fmt(_m, cur[_m])}
    for _lbl, _w in prior.items():
        _row[_lbl] = _pct(cur[_m], _w[_m])
    _comp_rows.append(_row)
st.caption("Each column is the percent change vs the 30-day window that ended that long ago (seasonality for the 1-year column).")
st.dataframe(pd.DataFrame(_comp_rows), width="stretch", hide_index=True)

st.divider()

# ===========================================================================
# Period detail (date-range driven) — scoped to the selected actor
# ===========================================================================
date_range = st.date_input("Period", value=(default_start, today), max_value=today)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = default_start, today

start_ts = pd.Timestamp(start_date, tz="UTC")
end_ts = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)

filtered = actor_posts[
    (actor_posts["published_at"] >= start_ts) & (actor_posts["published_at"] < end_ts)
].copy()

# ---- KPI row ----
def _sum(col: str) -> int:
    return int(filtered[col].fillna(0).sum()) if col in filtered.columns else 0


n_posts = len(filtered)
total_impressions = _sum("impression_count")
total_reactions = _sum("reaction_count")
total_comments = _sum("comment_count")
total_shares = _sum("share_count")
total_engagement = total_reactions + total_comments + total_shares
total_followers_gained = _sum("followers_gained_count")
avg_engagement_rate = (
    float(filtered["engagement_rate"].dropna().mean()) if "engagement_rate" in filtered.columns and not filtered["engagement_rate"].dropna().empty else 0
)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Posts", f"{n_posts:,}")
c2.metric("Impressions", f"{total_impressions:,}")
c3.metric("Comments", f"{total_comments:,}", help="Comments are usually the highest-signal engagement for B2B outreach.")
c4.metric("Reactions", f"{total_reactions:,}")
c5.metric("Avg engagement rate", f"{avg_engagement_rate:.2%}" if avg_engagement_rate < 1 else f"{avg_engagement_rate:.1f}%")

c6, c7, c8, _, _ = st.columns(5)
c6.metric("Shares", f"{total_shares:,}")
c7.metric("Followers gained", f"{total_followers_gained:,}")
c8.metric("Total engagement", f"{total_engagement:,}", help="Reactions + comments + shares.")

st.divider()

if filtered.empty:
    st.info("No posts in the selected window.")
    st.stop()

# ---- Top posts by comments ----
st.subheader("Top posts by comments")
top_by_comments = (
    filtered.sort_values("comment_count", ascending=False)
    .head(10)[["published_at", "actor_name", "text", "comment_count", "reaction_count",
               "share_count", "impression_count", "engagement_rate"]]
    .rename(columns={
        "published_at": "Published",
        "actor_name": "Actor",
        "text": "Post text",
        "comment_count": "Comments",
        "reaction_count": "Reactions",
        "share_count": "Shares",
        "impression_count": "Impressions",
        "engagement_rate": "Eng. rate",
    })
)
# Truncate post text for display
top_by_comments["Post text"] = top_by_comments["Post text"].str.slice(0, 140) + "…"
st.dataframe(
    top_by_comments, width="stretch", hide_index=True, height=380,
    column_config={
        "Post text": st.column_config.TextColumn(width="large"),
        "Eng. rate": st.column_config.NumberColumn(format="%.2f%%"),
    },
)

# ---- Charts: posting cadence + engagement over time ----
st.subheader("Posting cadence & engagement")
chart_data = filtered.copy()
chart_data["week"] = chart_data["published_at"].dt.to_period("W").dt.start_time
weekly = (
    chart_data.groupby("week").agg(
        posts=("urn", "count"),
        comments=("comment_count", "sum"),
        reactions=("reaction_count", "sum"),
        shares=("share_count", "sum"),
        impressions=("impression_count", "sum"),
    ).reset_index()
)

left, right = st.columns(2)
with left:
    st.markdown("**Posts per week**")
    fig = px.bar(weekly, x="week", y="posts")
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title=None, yaxis_title="Posts")
    st.plotly_chart(fig, width="stretch")

with right:
    st.markdown("**Engagement per week** (reactions + comments + shares)")
    weekly["engagement"] = weekly["reactions"] + weekly["comments"] + weekly["shares"]
    fig = px.bar(weekly, x="week", y="engagement",
                 color_discrete_sequence=["#1F4E79"])
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title=None, yaxis_title="Engagement")
    st.plotly_chart(fig, width="stretch")

# ---- Top posts by engagement rate ----
st.subheader("Highest engagement rate")
top_by_rate = (
    filtered[filtered["impression_count"].fillna(0) > 100]
    .sort_values("engagement_rate", ascending=False)
    .head(10)[["published_at", "text", "engagement_rate", "impression_count",
               "comment_count", "reaction_count"]]
    .rename(columns={
        "published_at": "Published",
        "text": "Post text",
        "engagement_rate": "Eng. rate",
        "impression_count": "Impressions",
        "comment_count": "Comments",
        "reaction_count": "Reactions",
    })
)
top_by_rate["Post text"] = top_by_rate["Post text"].str.slice(0, 140) + "…"
st.dataframe(
    top_by_rate, width="stretch", hide_index=True, height=300,
    column_config={
        "Post text": st.column_config.TextColumn(width="large"),
        "Eng. rate": st.column_config.NumberColumn(format="%.2f%%"),
    },
)
