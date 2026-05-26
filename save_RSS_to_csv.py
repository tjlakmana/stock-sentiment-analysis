# Import required libraries
import feedparser
import pandas as pd
from datetime import datetime

# RSS feed URL
rss_url = "https://feeds.marketwatch.com/marketwatch/topstories/"

# Parse RSS feed
feed = feedparser.parse(rss_url)

# Create empty list to store article data
articles = []

# Loop through first 10 articles
for entry in feed.entries:
    articles.append({
        "title": entry.title,
        "link": entry.link,
        "source": "MarketWatch",
        "collected_at": datetime.now()
    })

# Convert list into DataFrame
df = pd.DataFrame(articles)

# Save raw data to CSV
df.to_csv("raw_rss_articles.csv", index=False)

print("Raw RSS articles saved successfully!")
print(df.head())