# Import required libraries
import streamlit as st
import feedparser
import pandas as pd
from datetime import datetime

# Page title
st.title("RSS Feed Monitoring Dashboard")

# RSS feed URL
rss_url = "https://feeds.marketwatch.com/marketwatch/topstories/"

# Parse RSS feed
feed = feedparser.parse(rss_url)

# Store article data
articles = []

# Loop through RSS articles
for entry in feed.entries:
    articles.append({
        "title": entry.title,
        "link": entry.link,
        "source": "MarketWatch",
        "collected_at": datetime.now()
    })

# Convert to DataFrame
df = pd.DataFrame(articles)

# Dashboard metrics
st.subheader("Ingestion Metrics")

st.metric("Articles Collected", len(df))
st.metric("RSS Source", "MarketWatch")
st.metric("Feed Status", "Active" if len(df) > 0 else "No Data")

# Show article table
st.subheader("Collected Articles")
st.dataframe(df)

# Show article titles
st.subheader("Latest Headlines")

for index, row in df.iterrows():
    st.write(f"**{row['title']}**")
    st.write(row["link"])
    st.write("---")