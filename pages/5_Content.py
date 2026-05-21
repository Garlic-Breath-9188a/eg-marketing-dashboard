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

# Top posts by views (or by recency if no stats)
if has_stats:
    st.subheader("Top posts by views (last 30 days)")
    sort_col = "views_30d"
else:
    st.subheader("Most recent posts")
    sort_col = "published_at"

display = posts.sort_values(sort_col, ascending=False).head(20).copy()
display["Title"] = display["title"]
display["Categories"] = display["categories"]
display["Published"] = display["published_at"]
display["Author"] = display["author_name"]
display["URL"] = display["url"]
cols_to_show = ["Title", "Categories", "Author", "Published", "URL"]
if has_stats:
    display["Views 30d"] = display["views_30d"].fillna(0).astype(int)
    display["Views all time"] = display["views_all_time"].fillna(0).astype(int)
    cols_to_show = ["Title", "Views 30d", "Views all time", "Categories", "Published", "URL"]

st.dataframe(
    display[cols_to_show],
    use_container_width=True, hide_index=True, height=540,
    column_config={
        "Title": st.column_config.TextColumn(width="large"),
        "Categories": st.column_config.TextColumn(width="medium"),
        "Views 30d": st.column_config.NumberColumn(format="%d"),
        "Views all time": st.column_config.NumberColumn(format="%d"),
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
st.plotly_chart(fig, use_container_width=True)
