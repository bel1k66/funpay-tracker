import os
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

LIFECYCLE_FILE = "lot_lifecycle.csv"
BASE_URL = "https://funpay.com"
LIST_URL = "https://funpay.com/lots/85/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
SERVERS = ["eu west", "euw", "russia", "ru"]
REQUEST_TIMEOUT = (5, 10)
SLEEP_SECONDS = 0.2
MAX_RETRIES = 2
RETRY_BASE_DELAY_SECONDS = 0.6
MAX_LOTS_PER_RUN = 80
MAX_RUNTIME_SECONDS = 120

ROMAN_TO_INT = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
DIVISION_WORDS = {
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "перв": 1,
    "втор": 2,
    "трет": 3,
    "четв": 4,
}

LIFECYCLE_COLUMNS = [
    "lot_id",
    "rank_div",
    "price",
    "winrate",
    "winrate_status",
    "first_seen",
    "last_seen",
    "sold",
    "sold_duration_h",
]


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalize_division(raw: str) -> Optional[int]:
    token = _norm_space(raw)
    token = re.sub(r"[^a-zа-я0-9]", "", token)

    if token in DIVISION_WORDS:
        return DIVISION_WORDS[token]

    for prefix, div in (("перв", 1), ("втор", 2), ("трет", 3), ("четв", 4)):
        if token.startswith(prefix):
            return div

    if token in ROMAN_TO_INT:
        return ROMAN_TO_INT[token]

    if token.isdigit():
        value = int(token)
        if 1 <= value <= 4:
            return value

    return None


def extract_rank(text: str) -> Optional[str]:
    text = _norm_space(text)

    rank_aliases = {
        "emerald": ["emerald", "эмеральд", "изумруд"],
        "platinum": ["platinum", "платина"],
        "gold": ["gold", "золото"],
    }
    division_pattern = r"(1|2|3|4|i{1,3}|iv|перв\w*|втор\w*|трет\w*|четв\w*)"

    for rank_name, aliases in rank_aliases.items():
        alias_group = "(?:" + "|".join(re.escape(alias) for alias in aliases) + ")"
        patterns = [
            rf"{alias_group}\s*(?:див(?:изион)?\s*)?{division_pattern}\b",
            rf"{division_pattern}\s*(?:див(?:изион)?\s*)?{alias_group}\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue

            groups = [g for g in match.groups() if g]
            if not groups:
                continue

            for token in groups:
                division = _normalize_division(token)
                if division:
                    return f"{rank_name}_{division}"

        if re.search(alias_group, text):
            return f"{rank_name}_?"

    return None


def normalize_rank_div(value: object) -> object:
    if pd.isna(value):
        return pd.NA

    raw = _norm_space(str(value))
    if not raw:
        return pd.NA

    if re.fullmatch(r"(emerald|platinum|gold)_[1-4?]", raw):
        return raw

    extracted = extract_rank(raw)
    if extracted:
        return extracted

    if raw in {"emerald", "platinum", "gold"}:
        return f"{raw}_?"

    return raw


def extract_winrate(text: str) -> Tuple[Optional[int], str]:
    text = _norm_space(text)

    contextual_patterns = [
        r"(?:wr|win\s*rate|вин\s*рейт|винрейт|винр|вин рейт)\D{0,8}(\d{1,3})\s*%?",
        r"(\d{1,3})\s*%?\s*(?:wr|win\s*rate|вин\s*рейт|винрейт|винр|вин рейт)",
        r"винрейт\s*[:\-]?\s*(\d{1,3})",
    ]

    for pattern in contextual_patterns:
        for match in re.findall(pattern, text):
            val = int(match)
            if val == 0:
                return None, "zero_reported"
            if 1 <= val <= 100:
                return val, "parsed"

    percent_candidates = [int(x) for x in re.findall(r"(\d{1,3})\s*%", text)]
    unique_candidates = [x for x in percent_candidates if 0 <= x <= 100]

    if len(unique_candidates) == 1:
        val = unique_candidates[0]
        if val == 0:
            return None, "zero_reported"
        return val, "parsed_weak"

    return None, "missing"


def _safe_get(session: requests.Session, url: str) -> Optional[str]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait_seconds = float(retry_after) if retry_after else RETRY_BASE_DELAY_SECONDS * attempt
                except ValueError:
                    wait_seconds = RETRY_BASE_DELAY_SECONDS * attempt

                wait_seconds += random.uniform(0, 0.4)
                print(
                    f"[WARN] 429 для {url} (попытка {attempt}/{MAX_RETRIES}), "
                    f"ждём {wait_seconds:.2f} сек"
                )
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            is_last_attempt = attempt == MAX_RETRIES
            if is_last_attempt:
                print(f"[WARN] Не удалось загрузить {url}: {exc}")
                return None

            wait_seconds = RETRY_BASE_DELAY_SECONDS * attempt + random.uniform(0, 0.4)
            print(
                f"[WARN] Ошибка загрузки {url} (попытка {attempt}/{MAX_RETRIES}): {exc}. "
                f"Повтор через {wait_seconds:.2f} сек"
            )
            time.sleep(wait_seconds)

    return None


def collect_current_lots(now: datetime) -> Tuple[pd.DataFrame, bool]:
    current: List[Dict[str, object]] = []

    started_at = time.monotonic()

    with requests.Session() as session:
        listing_html = _safe_get(session, LIST_URL)
        if not listing_html:
            return pd.DataFrame(columns=["lot_id", "rank_div", "price", "winrate", "winrate_status", "seen"]), False

        soup = BeautifulSoup(listing_html, "lxml")
        lots = soup.find_all("a", class_="tc-item")

        for lot in lots:
            if len(current) >= MAX_LOTS_PER_RUN:
                print(f"[INFO] Достигнут лимит лотов за запуск: {MAX_LOTS_PER_RUN}")
                break

            elapsed = time.monotonic() - started_at
            if elapsed >= MAX_RUNTIME_SECONDS:
                print(f"[INFO] Достигнут лимит времени запуска: {MAX_RUNTIME_SECONDS} сек")
                break

            text = lot.get_text(" ", strip=True).lower()
            link = lot.get("href")
            if not link:
                continue

            lot_id_match = re.search(r"(\d+)", link)
            if not lot_id_match:
                continue
            lot_id = lot_id_match.group(1)

            lot_url = link if link.startswith("http") else BASE_URL + link
            detail_html = _safe_get(session, lot_url)
            if not detail_html:
                continue

            detail_soup = BeautifulSoup(detail_html, "lxml")
            desc_block = detail_soup.find("div", class_="tc-desc-text")
            desc_text = desc_block.get_text(" ", strip=True).lower() if desc_block else ""
            full_text = f"{text} {desc_text}"

            if "аренда" in full_text:
                continue
            if not any(server in full_text for server in SERVERS):
                continue

            rank_div = extract_rank(full_text)
            if not rank_div:
                continue

            price_match = re.search(r"(\d[\d\s]+)\s*₽", text)
            if not price_match:
                continue
            price = int(price_match.group(1).replace(" ", ""))

            winrate, winrate_status = extract_winrate(full_text)

            current.append(
                {
                    "lot_id": lot_id,
                    "rank_div": rank_div,
                    "price": price,
                    "winrate": winrate,
                    "winrate_status": winrate_status,
                    "seen": now,
                }
            )

            time.sleep(SLEEP_SECONDS)

    return pd.DataFrame(current), True


def load_lifecycle() -> pd.DataFrame:
    if os.path.exists(LIFECYCLE_FILE):
        life = pd.read_csv(LIFECYCLE_FILE)
    else:
        life = pd.DataFrame(columns=LIFECYCLE_COLUMNS)

    if "rank" in life.columns and "rank_div" not in life.columns:
        life = life.rename(columns={"rank": "rank_div"})

    defaults = {
        "lot_id": "",
        "rank_div": pd.NA,
        "price": pd.NA,
        "winrate": pd.NA,
        "winrate_status": "missing",
        "first_seen": pd.NaT,
        "last_seen": pd.NaT,
        "sold": False,
        "sold_duration_h": pd.NA,
    }

    for column in LIFECYCLE_COLUMNS:
        if column not in life.columns:
            life[column] = defaults[column]

    life = life[LIFECYCLE_COLUMNS].copy()
    life["lot_id"] = life["lot_id"].astype("string").fillna("")
    life["rank_div"] = life["rank_div"].map(normalize_rank_div)
    life["first_seen"] = pd.to_datetime(life["first_seen"], errors="coerce")
    life["last_seen"] = pd.to_datetime(life["last_seen"], errors="coerce")
    life["sold"] = life["sold"].fillna(False).astype(bool)
    life["winrate"] = pd.to_numeric(life["winrate"], errors="coerce")
    life["winrate_status"] = life["winrate_status"].fillna("missing").astype("string")
    life["sold_duration_h"] = pd.to_numeric(life["sold_duration_h"], errors="coerce")

    zero_mask = life["winrate"].fillna(-1) == 0
    life.loc[zero_mask, "winrate"] = pd.NA
    life.loc[zero_mask, "winrate_status"] = "zero_reported"

    missing_wr = life["winrate"].isna() & (life["winrate_status"] == "missing")
    life.loc[missing_wr, "winrate_status"] = "missing"

    sold_duration_missing = life["sold"] & life["sold_duration_h"].isna()
    life.loc[sold_duration_missing, "sold_duration_h"] = (
        life.loc[sold_duration_missing, "last_seen"] - life.loc[sold_duration_missing, "first_seen"]
    ).dt.total_seconds() / 3600

    return life


def update_lifecycle(current_df: pd.DataFrame, life: pd.DataFrame, now: datetime, fetch_ok: bool) -> pd.DataFrame:
    if not fetch_ok:
        print("[WARN] Список лотов не загружен, статусы sold не обновляем в этом запуске.")
        return life[LIFECYCLE_COLUMNS].copy()

    current_df = current_df.copy()
    if "lot_id" not in current_df.columns:
        current_df["lot_id"] = pd.Series(dtype="string")
    current_df["lot_id"] = current_df["lot_id"].astype("string")

    was_active = ~life["sold"]

    if current_df.empty:
        just_sold_mask = was_active.copy()
        life.loc[just_sold_mask, "sold"] = True
        life.loc[just_sold_mask, "sold_duration_h"] = (
            life.loc[just_sold_mask, "last_seen"] - life.loc[just_sold_mask, "first_seen"]
        ).dt.total_seconds() / 3600
        return life[LIFECYCLE_COLUMNS].copy()

    for _, row in current_df.iterrows():
        existing_mask = life["lot_id"] == row["lot_id"]
        if existing_mask.any():
            life.loc[existing_mask, ["last_seen", "sold", "sold_duration_h"]] = [now, False, pd.NA]
            life.loc[existing_mask, "rank_div"] = row["rank_div"]
            life.loc[existing_mask, "price"] = row["price"]
            if pd.notna(row.get("winrate")):
                life.loc[existing_mask, "winrate"] = row["winrate"]
                life.loc[existing_mask, "winrate_status"] = row.get("winrate_status", "parsed")
            elif row.get("winrate_status") == "zero_reported":
                life.loc[existing_mask, "winrate"] = pd.NA
                life.loc[existing_mask, "winrate_status"] = "zero_reported"
        else:
            life = pd.concat(
                [
                    life,
                    pd.DataFrame(
                        [
                            {
                                "lot_id": row["lot_id"],
                                "rank_div": row["rank_div"],
                                "price": row["price"],
                                "winrate": row["winrate"],
                                "winrate_status": row.get("winrate_status", "missing"),
                                "first_seen": now,
                                "last_seen": now,
                                "sold": False,
                                "sold_duration_h": pd.NA,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )

    active_ids = set(current_df["lot_id"])
    became_sold = was_active & ~life["lot_id"].isin(active_ids)
    life.loc[became_sold, "sold"] = True
    life.loc[became_sold, "sold_duration_h"] = (
        life.loc[became_sold, "last_seen"] - life.loc[became_sold, "first_seen"]
    ).dt.total_seconds() / 3600

    return life[LIFECYCLE_COLUMNS].copy()


def print_liquidity(life: pd.DataFrame) -> None:
    life = life[LIFECYCLE_COLUMNS].copy()

    print("\n=== ЛИКВИДНОСТЬ ПО ДИВИЗИОНАМ ===")

    sold_lots = life[life["sold"]].copy()
    if sold_lots.empty:
        print("Продаж пока нет.")
        return

    sold_lots["lifetime_h"] = sold_lots["sold_duration_h"].fillna(
        (pd.to_datetime(sold_lots["last_seen"]) - pd.to_datetime(sold_lots["first_seen"])).dt.total_seconds() / 3600
    )

    for div in sorted(sold_lots["rank_div"].dropna().unique()):
        part = sold_lots[sold_lots["rank_div"] == div]
        median_price = int(part["price"].median()) if part["price"].notna().any() else 0
        median_time = round(part["lifetime_h"].median(), 2) if part["lifetime_h"].notna().any() else 0

        print(
            f"{str(div).upper()} | продано: {len(part)} | "
            f"цена: {median_price} ₽ | "
            f"время продажи: {median_time} ч"
        )


def main() -> None:
    now = datetime.now()
    current_df, fetch_ok = collect_current_lots(now)
    life = load_lifecycle()
    life = update_lifecycle(current_df, life, now, fetch_ok=fetch_ok)

    life[LIFECYCLE_COLUMNS].to_csv(LIFECYCLE_FILE, index=False)

    print(f"\nТекущих лотов: {len(current_df)}")
    print(f"Всего в истории: {len(life)}")
    known_wr = life["winrate"].notna().sum()
    zero_wr = (life["winrate_status"] == "zero_reported").sum()
    print(f"Winrate заполнен: {known_wr}, нулевой/сомнительный: {zero_wr}")
    print_liquidity(life)


if __name__ == "__main__":
    main()
