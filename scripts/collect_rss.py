import feedparser
import pandas as pd
from datetime import datetime
import os

RSS_FEEDS = {
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex"
}

articles = []

for source, url in RSS_FEEDS.items():
    feed = feedparser.parse(url)

    for entry in feed.entries:
        articles.append({
            "title": entry.get("title", ""),
            "summary": entry.get("summary", ""),
            "link": entry.get("link", ""),
            "source": source,
            "published": entry.get("published", ""),
            "collected_at": datetime.now()
        })

df = pd.DataFrame(articles)

os.makedirs("data", exist_ok=True)

df.to_csv("data/raw_rss_articles.csv", index=False)

print("RSS articles collected successfully.")
print(f"Total articles collected: {len(df)}")
print(df.head())