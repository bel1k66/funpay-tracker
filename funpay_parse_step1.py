import requests
from bs4 import BeautifulSoup
import re
import pandas as pd
from datetime import datetime
import os

LIFECYCLE_FILE = "lot_lifecycle.csv"

url = "https://funpay.com/lots/85/"
headers = {"User-Agent": "Mozilla/5.0"}

r = requests.get(url, headers=headers)
soup = BeautifulSoup(r.text, "lxml")

lots = soup.find_all("a", class_="tc-item")

servers = ["eu west", "euw", "russia", "ru"]
ranks = ["gold", "золото", "platinum", "платина", "emerald", "эмеральд"]

now = datetime.now()

current = []

for lot in lots:
    text = lot.get_text(" ", strip=True).lower()

    link = lot.get("href")
    if not link:
        continue
    lot_id = re.findall(r"\d+", link)[0]

    # === ЗАХОДИМ ВНУТРЬ ЛОТА ===
    lot_url = "https://funpay.com" + link
    detail_r = requests.get(lot_url, headers=headers)
    detail_soup = BeautifulSoup(detail_r.text, "lxml")

    description_block = detail_soup.find("div", class_="tc-desc-text")
    description_text = description_block.get_text(" ", strip=True).lower() if description_block else ""

    full_text = text + " " + description_text

    # === ФИЛЬТРЫ ===
    if "аренда" in full_text:
        continue
    if not any(s in full_text for s in servers):
        continue
    if not any(r in full_text for r in ranks):
        continue


price_match = re.search(r"(\d[\d\s]+)\s*₽", text)
if not price_match:
    continue
price = int(price_match.group(1).replace(" ", ""))

# === WINRATE ===
wr_patterns = [
    r"(\d{2})\s*%?\s*(wr|winrate)",
    r"(wr)\s*(\d{2})",
    r"(\d{2})\s*%",
]

winrate = None

for pattern in wr_patterns:
    match = re.search(pattern, full_text)
    if match:
        nums = re.findall(r"\d{2}", match.group())
        if nums:
            value = int(nums[0])
            if 30 <= value <= 100:
                winrate = value
                break

# === RANK ===
if "emerald" in full_text or "эмеральд" in full_text or "изумруд" in full_text:
    rank = "emerald"
elif "platinum" in full_text or "платина" in full_text:
    rank = "platinum"
elif "gold" in full_text or "золото" in full_text:
    rank = "gold"
else:
    continue


    current.append({
        "lot_id": lot_id,
        "rank": rank,
        "price": price,
        "winrate": winrate,
        "seen": now
    })

current_df = pd.DataFrame(current)

if os.path.exists(LIFECYCLE_FILE):
    life = pd.read_csv(LIFECYCLE_FILE, parse_dates=["first_seen", "last_seen"])
else:
    life = pd.DataFrame(columns=[
    "lot_id","rank","price","winrate","first_seen","last_seen","sold"
])


# обновляем существующие лоты
for _, row in current_df.iterrows():
    if row["lot_id"] in life["lot_id"].values:
        life.loc[life["lot_id"] == row["lot_id"], "last_seen"] = now
    else:
        life = pd.concat([life, pd.DataFrame([{
            "lot_id": row["lot_id"],
            "rank": row["rank"],
            "price": row["price"],
            "winrate": row["winrate"],
            "first_seen": now,
            "last_seen": now,
            "sold": False
        }])], ignore_index=True)

# помечаем проданные
active_ids = set(current_df["lot_id"])
life.loc[~life["lot_id"].isin(active_ids), "sold"] = True

life.to_csv(LIFECYCLE_FILE, index=False)

sold_now = life[(life["sold"] == True) & (life["last_seen"] == now)]

print("\nТекущих лотов:", len(current_df))
print("Всего в истории:", len(life))
print("Продано за этот запуск:", len(sold_now))

print("\n=== ОТЧЁТ ПО ЛИКВИДНОСТИ ===")

life["first_seen"] = pd.to_datetime(life["first_seen"])
life["last_seen"] = pd.to_datetime(life["last_seen"])

sold_lots = life[life["sold"] == True].copy()

if sold_lots.empty:
    print("Продаж пока нет.")
else:
    sold_lots["lifetime_hours"] = (
        sold_lots["last_seen"] - sold_lots["first_seen"]
    ).dt.total_seconds() / 3600

    for rank in ["gold", "platinum", "emerald"]:
        part = sold_lots[sold_lots["rank"] == rank]

        if part.empty:
            print(f"{rank}: продаж нет")
            continue

        median_price = int(part["price"].median())
        median_time = round(part["lifetime_hours"].median(), 2)

        print(
            f"{rank.upper()} | продано: {len(part)} | "
            f"медиана цены: {median_price} ₽ | "
            f"медиана времени продажи: {median_time} ч"
        )


