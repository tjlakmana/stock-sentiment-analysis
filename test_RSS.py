import feedparser

rss_url = "https://feeds.marketwatch.com/marketwatch/topstories/"

feed = feedparser.parse(rss_url)

for entry in feed.entries[:5]:
    print(entry.title)
    print(entry.link)
    print("-" * 50)