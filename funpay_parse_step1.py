diff --git a/market_parser.py b/market_parser.py
new file mode 100644
index 0000000000000000000000000000000000000000..98105121ace7b9d0f94ae5a6038cf0ba698d4a82
--- /dev/null
+++ b/market_parser.py
@@ -0,0 +1,221 @@
+import os
+import re
+import time
+from datetime import datetime
+from typing import Dict, List, Optional
+
+import pandas as pd
+import requests
+from bs4 import BeautifulSoup
+
+LIFECYCLE_FILE = "lot_lifecycle.csv"
+BASE_URL = "https://funpay.com"
+LIST_URL = "https://funpay.com/lots/85/"
+HEADERS = {"User-Agent": "Mozilla/5.0"}
+SERVERS = ["eu west", "euw", "russia", "ru"]
+REQUEST_TIMEOUT = 15
+SLEEP_SECONDS = 0.15
+
+
+def extract_rank(text: str) -> Optional[str]:
+    patterns = [
+        (r"(emerald|эмеральд|изумруд)\s*([1-9])", "emerald"),
+        (r"(platinum|платина)\s*([1-9])", "platinum"),
+        (r"(gold|золото)\s*([1-9])", "gold"),
+    ]
+
+    for pattern, rank in patterns:
+        match = re.search(pattern, text)
+        if match:
+            return f"{rank}_{match.group(2)}"
+
+    return None
+
+
+def extract_winrate(text: str) -> Optional[int]:
+    wr_patterns = [
+        r"(\d{2,3})\s*%?\s*(wr|winrate)",
+        r"(wr|winrate)\s*(\d{2,3})",
+        r"(\d{2,3})\s*%",
+    ]
+
+    for pattern in wr_patterns:
+        match = re.search(pattern, text)
+        if not match:
+            continue
+
+        nums = re.findall(r"\d{2,3}", match.group())
+        if not nums:
+            continue
+
+        val = int(nums[0])
+        if 30 <= val <= 100:
+            return val
+
+    return None
+
+
+def _safe_get(session: requests.Session, url: str) -> Optional[str]:
+    try:
+        response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
+        response.raise_for_status()
+        return response.text
+    except requests.RequestException as exc:
+        print(f"[WARN] Не удалось загрузить {url}: {exc}")
+        return None
+
+
+def collect_current_lots(now: datetime) -> pd.DataFrame:
+    current: List[Dict[str, object]] = []
+
+    with requests.Session() as session:
+        listing_html = _safe_get(session, LIST_URL)
+        if not listing_html:
+            return pd.DataFrame(columns=["lot_id", "rank_div", "price", "winrate", "seen"])
+
+        soup = BeautifulSoup(listing_html, "lxml")
+        lots = soup.find_all("a", class_="tc-item")
+
+        for lot in lots:
+            text = lot.get_text(" ", strip=True).lower()
+            link = lot.get("href")
+            if not link:
+                continue
+
+            lot_id_match = re.search(r"(\d+)", link)
+            if not lot_id_match:
+                continue
+            lot_id = lot_id_match.group(1)
+
+            lot_url = link if link.startswith("http") else BASE_URL + link
+            detail_html = _safe_get(session, lot_url)
+            if not detail_html:
+                continue
+
+            detail_soup = BeautifulSoup(detail_html, "lxml")
+            desc_block = detail_soup.find("div", class_="tc-desc-text")
+            desc_text = desc_block.get_text(" ", strip=True).lower() if desc_block else ""
+            full_text = f"{text} {desc_text}"
+
+            if "аренда" in full_text:
+                continue
+            if not any(server in full_text for server in SERVERS):
+                continue
+
+            rank_div = extract_rank(full_text)
+            if not rank_div:
+                continue
+
+            price_match = re.search(r"(\d[\d\s]+)\s*₽", text)
+            if not price_match:
+                continue
+            price = int(price_match.group(1).replace(" ", ""))
+
+            current.append(
+                {
+                    "lot_id": lot_id,
+                    "rank_div": rank_div,
+                    "price": price,
+                    "winrate": extract_winrate(full_text),
+                    "seen": now,
+                }
+            )
+
+            time.sleep(SLEEP_SECONDS)
+
+    return pd.DataFrame(current)
+
+
+LIFECYCLE_COLUMNS = ["lot_id", "rank_div", "price", "winrate", "first_seen", "last_seen", "sold"]
+
+
+def load_lifecycle() -> pd.DataFrame:
+    if os.path.exists(LIFECYCLE_FILE):
+        life = pd.read_csv(LIFECYCLE_FILE)
+    else:
+        life = pd.DataFrame(columns=LIFECYCLE_COLUMNS)
+
+    # Миграция старой схемы: rank -> rank_div.
+    if "rank" in life.columns and "rank_div" not in life.columns:
+        life = life.rename(columns={"rank": "rank_div"})
+
+    defaults = {
+        "lot_id": "",
+        "rank_div": pd.NA,
+        "price": pd.NA,
+        "winrate": pd.NA,
+        "first_seen": pd.NaT,
+        "last_seen": pd.NaT,
+        "sold": False,
+    }
+
+    for column in LIFECYCLE_COLUMNS:
+        if column not in life.columns:
+            life[column] = defaults[column]
+
+    # Нормализуем типы и оставляем только целевую схему.
+    life = life[LIFECYCLE_COLUMNS].copy()
+    life["lot_id"] = life["lot_id"].astype("string").fillna("")
+    life["first_seen"] = pd.to_datetime(life["first_seen"], errors="coerce")
+    life["last_seen"] = pd.to_datetime(life["last_seen"], errors="coerce")
+    life["sold"] = life["sold"].fillna(False).astype(bool)
+
+    return life
+
+
+def update_lifecycle(current_df: pd.DataFrame, life: pd.DataFrame, now: datetime) -> pd.DataFrame:
+    current_df = current_df.copy()
+    if "lot_id" not in current_df.columns:
+        current_df["lot_id"] = pd.Series(dtype="string")
+    current_df["lot_id"] = current_df["lot_id"].astype("string")
+
+    if current_df.empty:
+        life.loc[:, "sold"] = True
+        return life[LIFECYCLE_COLUMNS].copy()
+
+    for _, row in current_df.iterrows():
+        existing_mask = life["lot_id"] == row["lot_id"]
+        if existing_mask.any():
+            life.loc[existing_mask, ["last_seen", "sold"]] = [now, False]
+        else:
+            life = pd.concat(
+                [
+                    life,
+                    pd.DataFrame(
+                        [
+                            {
+                                "lot_id": row["lot_id"],
+                                "rank_div": row["rank_div"],
+                                "price": row["price"],
+                                "winrate": row["winrate"],
+                                "first_seen": now,
+                                "last_seen": now,
+                                "sold": False,
+                            }
+                        ]
+                    ),
+                ],
+                ignore_index=True,
+            )
+
+    active_ids = set(current_df["lot_id"])
+    life.loc[~life["lot_id"].isin(active_ids), "sold"] = True
+
+    return life[LIFECYCLE_COLUMNS].copy()
+
+
+def print_liquidity(life: pd.DataFrame) -> None:
+    life = life[LIFECYCLE_COLUMNS].copy()
+
+    print("\n=== ЛИКВИДНОСТЬ ПО ДИВИЗИОНАМ ===")
+
+    sold_lots = life[life["sold"] == True].copy()  # noqa: E712
+    if sold_lots.empty:
+        print("Продаж пока нет.")
+        return
+
+    sold_lots["lifetime_h"] = (
+        pd.to_datetime(sold_lots["last_seen"]) - pd.to_datetime(sold_lots["first_seen"])
+    ).dt.total_seconds() / 3600
+
+    for div in sorted(sold_lots["rank_div"].dropna().unique()):
+        part = sold_lots[sold_lots["rank_div"] == div]
+        median_price = int(part["price"].median())
+        median_time = round(part["lifetime_h"].median(), 2)
+
+        print(
+            f"{str(div).upper()} | продано: {len(part)} | "
+            f"цена: {median_price} ₽ | "
+            f"время: {median_time} ч"
+        )
+
+
+def main() -> None:
+    now = datetime.now()
+    current_df = collect_current_lots(now)
+    life = load_lifecycle()
+    life = update_lifecycle(current_df, life, now)
+
+    life[LIFECYCLE_COLUMNS].to_csv(LIFECYCLE_FILE, index=False)
+
+    print(f"\nТекущих лотов: {len(current_df)}")
+    print(f"Всего в истории: {len(life)}")
+    print_liquidity(life)
+
+
+if __name__ == "__main__":
+    main()
