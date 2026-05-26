# Import required libraries
import pandas as pd
import re

# Load CSV file
df = pd.read_csv("raw_rss_articles.csv")

# Example company-to-ticker mapping
company_tickers = {
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "apple": "AAPL",
    "amazon": "AMZN",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "micron": "MU",
    "ubs": "UBS",
    "starlink": "PRIVATE"
}

# Function to extract ticker symbols
def extract_ticker(title):

    # Convert title to lowercase
    title_lower = title.lower()

    # Search for company names
    for company, ticker in company_tickers.items():

        if company in title_lower:
            return ticker

    # Search for cashtags like $TSLA
    cashtag_match = re.findall(r'\\$[A-Za-z]+', title)

    if cashtag_match:
        return cashtag_match[0]

    return "No ticker found"

# Apply ticker extraction
df["ticker"] = df["title"].apply(extract_ticker)

# Print results
print(df[["title", "ticker"]])