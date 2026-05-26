import pandas as pd
import re
import os

df = pd.read_csv("data/raw_rss_articles.csv")

def clean_text(text):
    if pd.isna(text):
        return ""

    text = str(text).lower()
    text = re.sub(r"http\S+", " ", text)
    text = re.sub(r"<.*?>", " ", text)
    text = re.sub(r"[^a-zA-Z0-9\s$]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text

df["clean_title"] = df["title"].apply(clean_text)
df["clean_summary"] = df["summary"].apply(clean_text)

df = df.drop_duplicates(subset=["title", "link"])

os.makedirs("data", exist_ok=True)

df.to_csv("data/processed_articles.csv", index=False)

print("Text preprocessing completed successfully.")
print(df[["title", "clean_title"]].head())