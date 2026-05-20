"""LinkedIn page — post performance from AuthoredUp."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from store import db

st.set_page_config(page_title="LinkedIn — EG Marketing Dashboard", page_icon="💼", layout="wide")

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

# ---- Filters ----
today = datetime.now(timezone.utc).date()
default_start = today - timedelta(days=90)

col_actor, col_date = st.columns([0.4, 0.6])

with col_actor:
    actor_options = ["All actors"] + actors["name"].fillna("(unknown)").tolist()
    selected_actor = st.selectbox("Actor", options=actor_options, index=0)

with col_date:
    date_range = st.date_input(
        "Period",
        value=(default_start, today),
        max_value=today,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = default_start, today

start_ts = pd.Timestamp(start_date, tz="UTC")
end_ts = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)

filtered = posts[
    (posts["published_at"] >= start_ts) & (posts["published_at"] < end_ts)
].copy()
if selected_actor != "All actors":
    actor_id = actors[actors["name"] == selected_actor]["id"].iloc[0]
    filtered = filtered[filtered["actor_id"] == actor_id]

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
    top_by_comments, use_container_width=True, hide_index=True, height=380,
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
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.markdown("**Engagement per week** (reactions + comments + shares)")
    weekly["engagement"] = weekly["reactions"] + weekly["comments"] + weekly["shares"]
    fig = px.bar(weekly, x="week", y="engagement",
                 color_discrete_sequence=["#1F4E79"])
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_title=None, yaxis_title="Engagement")
    st.plotly_chart(fig, use_container_width=True)

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
    top_by_rate, use_container_width=True, hide_index=True, height=300,
    column_config={
        "Post text": st.column_config.TextColumn(width="large"),
        "Eng. rate": st.column_config.NumberColumn(format="%.2f%%"),
    },
)
