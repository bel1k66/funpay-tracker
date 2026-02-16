import requests
from bs4 import BeautifulSoup
import re
import pandas as pd
from datetime import datetime
import os
import time

LIFECYCLE_FILE = "lot_lifecycle.csv"

BASE_URL = "https://funpay.com"
LIST_URL = "https://funpay.com/lots/85/"

headers = {"User-Agent": "Mozilla/5.0"}

r = requests.get(LIST_URL, headers=headers)
soup = BeautifulSoup(r.text, "lxml")

lots = soup.find_all("a", class_="tc-item")

servers = ["eu west", "euw", "russia", "ru"]

now = datetime.now()
current = []

def extract_rank(text):
    patterns = [
        (r"(emerald|эмеральд|изумруд)\s*(\d)", "emerald"),
        (r"(platinum|платина)\s*(\d)", "platinum"),
        (r"(gold|золото)\s*(\d)", "gold"),
    ]

    for pattern, rank in patterns:
        m = re.search(pattern, text)
        if m:
            return f"{rank}_{m.group(2)}"

    return None


def extract_winrate(text):
    wr_patterns = [
        r"(\d{2})\s*%?\s*(wr|winrate)",
        r"(wr)\s*(\d{2})",
        r"(\d{2})\s*%",
    ]

    for pattern in wr_patterns:
        match = re.search(pattern, text)
        if match:
            nums = re.findall(r"\d{2}", match.group())
            if nums:
                val = int(nums[0])
                if 30 <= val <= 100:
                    return val
    return None


for lot in lots:
    text = lot.get_text(" ", strip=True).lower()

    link = lot.get("href")
    if not link:
        continue
    lot_id = re.findall(r"\d+", link)[0]

    if link.startswith("http"):
    lot_url = link
else:
    if link.startswith("http"):
    lot_url = link
else:
    lot_url = BASE_URL + link

detail_r = requests.get(lot_url, headers=headers)
    detail_soup = BeautifulSoup(detail_r.text, "lxml")

    desc_block = detail_soup.find("div", class_="tc-desc-text")
    desc_text = desc_block.get_text(" ", strip=True).lower() if desc_block else ""

    full_text = text + " " + desc_text

    if "аренда" in full_text:
        continue
    if not any(s in full_text for s in servers):
        continue

    rank_div = extract_rank(full_text)
    if not rank_div:
        continue

    price_match = re.search(r"(\d[\d\s]+)\s*₽", text)
    if not price_match:
        continue
    price = int(price_match.group(1).replace(" ", ""))

    winrate = extract_winrate(full_text)

    current.append({
        "lot_id": lot_id,
        "rank_div": rank_div,
        "price": price,
        "winrate": winrate,
        "seen": now
    })

    time.sleep(0.15)  # защита от частых запросов


current_df = pd.DataFrame(current)

if os.path.exists(LIFECYCLE_FILE):
    life = pd.read_csv(LIFECYCLE_FILE, parse_dates=["first_seen", "last_seen"])
else:
    life = pd.DataFrame(columns=[
        "lot_id","rank_div","price","winrate","first_seen","last_seen","sold"
    ])


for _, row in current_df.iterrows():
    if row["lot_id"] in life["lot_id"].values:
        life.loc[life["lot_id"] == row["lot_id"], "last_seen"] = now
    else:
        life = pd.concat([life, pd.DataFrame([{
            "lot_id": row["lot_id"],
            "rank_div": row["rank_div"],
            "price": row["price"],
            "winrate": row["winrate"],
            "first_seen": now,
            "last_seen": now,
            "sold": False
        }])], ignore_index=True)


active_ids = set(current_df["lot_id"])
life.loc[~life["lot_id"].isin(active_ids), "sold"] = True

life.to_csv(LIFECYCLE_FILE, index=False)


print("\nТекущих лотов:", len(current_df))
print("Всего в истории:", len(life))


print("\n=== ЛИКВИДНОСТЬ ПО ДИВИЗИОНАМ ===")

sold_lots = life[life["sold"] == True].copy()

if sold_lots.empty:
    print("Продаж пока нет.")
else:
    sold_lots["lifetime_h"] = (
        pd.to_datetime(sold_lots["last_seen"]) -
        pd.to_datetime(sold_lots["first_seen"])
    ).dt.total_seconds() / 3600

    for div in sorted(sold_lots["rank_div"].unique()):
        part = sold_lots[sold_lots["rank_div"] == div]

        median_price = int(part["price"].median())
        median_time = round(part["lifetime_h"].median(), 2)

        print(
            f"{div.upper()} | продано: {len(part)} | "
            f"цена: {median_price} ₽ | "
            f"время: {median_time} ч"
        )

