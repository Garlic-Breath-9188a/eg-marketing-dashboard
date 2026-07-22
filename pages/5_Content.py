"""Content page — WealthTechToday.com posts and (optional) Jetpack stats."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from store import db

st.set_page_config(page_title="Content — EG Marketing Dashboard", page_icon="📰", layout="wide")

if not st.session_state.get("authed"):
    st.warning("Sign in on the main page first.")
    st.stop()


@st.cache_data(ttl=3600)
def load_wp_posts() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        try:
            df = pd.read_sql("SELECT * FROM wordpress_posts", conn)
        except Exception:
            return pd.DataFrame()
    if "published_at" in df.columns:
        df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True)
    return df


st.title("📰 Content Performance")
st.caption("WealthTechToday.com posts. View counts require Jetpack Stats (WordPress.com token).")

posts = load_wp_posts()

if posts.empty:
    st.warning(
        "No WordPress data yet. Configure `WORDPRESS_BASE_URL`, `WORDPRESS_USER`, "
        "and `WORDPRESS_APP_PASSWORD` in secrets, then click **🔄 Refresh from WordPress** "
        "in the sidebar of the Overview page."
    )
    st.stop()

has_stats = posts["views_30d"].fillna(0).sum() > 0 or posts["views_all_time"].fillna(0).sum() > 0

# KPIs
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total posts", f"{len(posts):,}")
last_30d_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
posts_last_30d = int((posts["published_at"] >= last_30d_cutoff).sum())
c2.metric("Posts last 30d", f"{posts_last_30d:,}")
if has_stats:
    c3.metric("Views (30d)", f"{int(posts['views_30d'].fillna(0).sum()):,}")
    c4.metric("Views (all time)", f"{int(posts['views_all_time'].fillna(0).sum()):,}")
else:
    c3.metric("Views (30d)", "—", help="Jetpack stats not configured.")
    c4.metric("Views (all time)", "—", help="Jetpack stats not configured.")

st.divider()

# Posts, ranked by views. Views come from Jetpack Stats, which needs a
# WordPress.com token — without it every post reads 0 and sorting by views
# would produce an arbitrary order, so fall back to newest-first and say so
# rather than presenting a meaningless ranking as a ranking.
if has_stats:
    st.subheader("Posts by views (last 30 days)")
    posts_sorted = posts.sort_values("views_30d", ascending=False, na_position="last")
else:
    st.subheader("Posts — newest first")
    st.info(
        "**View counts are unavailable**, so these are sorted by date instead. "
        "Views need Jetpack Stats: create a token at "
        "[developer.wordpress.com/apps](https://developer.wordpress.com/apps), then add "
        "`WPCOM_API_TOKEN` and `WPCOM_SITE = \"wealthtechtoday.com\"` to "
        "`.streamlit/secrets.toml` and refresh WordPress. The ingest already "
        "fetches views when the token is present — nothing else to build.",
        icon="📊",
    )
    posts_sorted = posts.sort_values("published_at", ascending=False, na_position="last")

display = posts_sorted.head(20).copy()
display["Title"] = display["title"]
display["Views"] = display["views_30d"].fillna(0).astype(int)
display["Categories"] = display["categories"]
# Date only — the stored value is a full UTC timestamp, and the time of day
# carries no meaning for a published post.
display["Published"] = display["published_at"].dt.strftime("%Y-%m-%d")
display["URL"] = display["url"]

st.dataframe(
    display[["Title", "Views", "Categories", "Published", "URL"]],
    width="stretch", hide_index=True, height=540,
    column_config={
        "Title": st.column_config.TextColumn(width="large"),
        "Views": st.column_config.NumberColumn(
            "Views (30d)", format="%d", width="small",
            help=None if has_stats else "Jetpack Stats not connected — all zero.",
        ),
        "Categories": st.column_config.TextColumn(width="medium"),
        "Published": st.column_config.TextColumn(width="small"),
        "URL": st.column_config.LinkColumn("Open", display_text="↗", width="small"),
    },
)

# Posting cadence
st.divider()
st.subheader("Posting cadence")
posts_with_date = posts[posts["published_at"].notna()].copy()
posts_with_date["month"] = posts_with_date["published_at"].dt.to_period("M").dt.start_time
monthly = posts_with_date.groupby("month").size().reset_index(name="posts")
fig = px.bar(monthly.tail(24), x="month", y="posts")
fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10),
                  xaxis_title=None, yaxis_title="Posts published")
st.plotly_chart(fig, width="stretch")
