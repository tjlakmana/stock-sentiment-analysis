# Import required libraries
import feedparser
import re

# RSS feed URL from MarketWatch
rss_url = "https://feeds.marketwatch.com/marketwatch/topstories/"

# Parse the RSS feed
feed = feedparser.parse(rss_url)

# Function to clean text data
def clean_text(text):

    # Convert text to lowercase
    text = text.lower()

    # Remove URLs
    text = re.sub(r"http\\S+", "", text)

    # Remove punctuation and special characters
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", text)

    return text

# Loop through first 5 news headlines
for entry in feed.entries[:5]:

    # Store original headline
    original = entry.title

    # Clean headline text
    cleaned = clean_text(original)

    # Print original headline
    print("ORIGINAL:")
    print(original)

    # Print cleaned headline
    print("\nCLEANED:")
    print(cleaned)

    # Print separator line
    print("-" * 60)