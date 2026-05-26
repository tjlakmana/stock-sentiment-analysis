import pandas as pd
import re
import os

df = pd.read_csv("data/processed_articles.csv")

company_tickers = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "netflix": "NFLX",
    "micron": "MU",
    "starlink": "PRIVATE",
    "ubs": "UBS",
    "boeing": "BA",
    "disney": "DIS",
    "walmart": "WMT",
    "target": "TGT",
    "intel": "INTC",
    "amd": "AMD",
    "ford": "F",
    "general motors": "GM"
}

def extract_ticker(text):
    if pd.isna(text):
        return "No ticker found"

    text_lower = str(text).lower()

    cashtags = re.findall(r"\$[A-Za-z]{1,5}", str(text))
    if cashtags:
        return cashtags[0].replace("$", "").upper()

    for company, ticker in company_tickers.items():
        if company in text_lower:
            return ticker

    return "No ticker found"

df["ticker"] = df["clean_title"].apply(extract_ticker)

os.makedirs("data", exist_ok=True)

df.to_csv("data/articles_with_tickers.csv", index=False)

print("Ticker extraction completed successfully.")
print(df[["title", "ticker"]].head(15))