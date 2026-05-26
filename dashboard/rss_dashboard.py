import streamlit as st
import pandas as pd
import os

st.set_page_config(
    page_title="RSS Feed Dashboard",
    layout="wide"
)

st.title("RSS Feed Monitoring Dashboard")

file_path = "data/articles_with_tickers.csv"

if not os.path.exists(file_path):
    st.warning("No processed data found. Run the scripts first.")
    st.stop()

df = pd.read_csv(file_path)

st.subheader("Ingestion Metrics")

col1, col2, col3, col4 = st.columns(4)

col1.metric("Total Articles", len(df))
col2.metric("Sources", df["source"].nunique())
col3.metric("Tickers Found", (df["ticker"] != "No ticker found").sum())
col4.metric("Unresolved Articles", (df["ticker"] == "No ticker found").sum())

st.subheader("Article Filters")

source_filter = st.selectbox(
    "Filter by source",
    ["All"] + sorted(df["source"].dropna().unique().tolist())
)

search_term = st.text_input("Search headlines")

filtered_df = df.copy()

if source_filter != "All":
    filtered_df = filtered_df[filtered_df["source"] == source_filter]

if search_term:
    filtered_df = filtered_df[
        filtered_df["title"].str.contains(search_term, case=False, na=False)
    ]

st.subheader("Articles by Source")
st.bar_chart(filtered_df["source"].value_counts())

st.subheader("Ticker Counts")
st.bar_chart(filtered_df["ticker"].value_counts())

st.subheader("Collected Articles")
st.dataframe(
    filtered_df[["title", "source", "ticker", "published", "link"]],
    use_container_width=True
)

st.subheader("Latest Headlines")

for _, row in filtered_df.head(10).iterrows():
    st.write(f"**{row['title']}**")
    st.write(f"Source: {row['source']} | Ticker: {row['ticker']}")
    st.write(row["link"])
    st.write("---")