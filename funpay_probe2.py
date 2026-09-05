"""
FunPay probe v2 — разведка перед переписыванием трекера.
Запускать локально, рядом с lot_lifecycle.csv. Ничего не меняет, только смотрит.

    python funpay_probe2.py

Отвечает на 4 вопроса:
  1. Что реально лежит в карточке лота (поля, data-атрибуты, цена).
  2. Есть ли у категории фильтры (например по рангу) и как они уходят в URL.
  3. Живёт ли страница лота после исчезновения из листинга  <-- главный вопрос.
  4. Что видно на странице продавца (отзывы: категория / сумма / дата).
"""
import json
import random
import re
import sys
import time
from collections import Counter

import requests
from bs4 import BeautifulSoup

CAT = sys.argv[1] if len(sys.argv) > 1 else "85"
LIST_URL = f"https://funpay.com/lots/{CAT}/"
OFFER_URL = "https://funpay.com/lots/offer?id={}"
CSV_FILE = "lot_lifecycle.csv"
PAUSE = 1.2  # секунды между запросами, не уменьшать

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

RANK_WORDS = [
    "iron", "bronze", "silver", "gold", "platinum", "emerald", "diamond",
    "master", "grandmaster", "challenger", "unranked",
    "железо", "бронза", "серебро", "золото", "платина", "изумруд", "эмеральд",
    "алмаз", "даймонд", "мастер", "грандмастер", "челленджер",
    "без ранга", "безранг", "анранк", "смурф", "smurf",
]


def head(title):
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


def get(session, url):
    r = session.get(url, headers=HEADERS, timeout=(6, 20), allow_redirects=True)
    time.sleep(PAUSE + random.uniform(0, 0.4))
    return r


# ---------------------------------------------------------------- 1. листинг
def probe_listing(session):
    head("1. ЛИСТИНГ КАТЕГОРИИ")
    r = get(session, LIST_URL)
    print(f"URL          : {LIST_URL}")
    print(f"HTTP         : {r.status_code}")
    print(f"Итоговый URL : {r.url}")
    print(f"Размер       : {len(r.text)} байт")
    print(f"Server       : {r.headers.get('Server')}")
    print(f"Set-Cookie   : {'да' if r.cookies else 'нет'}")

    with open(f"funpay_{CAT}.html", "w", encoding="utf-8") as f:
        f.write(r.text)
    print(f"HTML сохранён: funpay_{CAT}.html")

    if r.status_code != 200:
        print("\n[!] Не 200. Дальше смысла нет — сначала разбираемся с доступом.")
        return None, []

    soup = BeautifulSoup(r.text, "lxml")
    items = soup.select("a.tc-item") or soup.select(".tc-item")
    print(f"\nКарточек найдено: {len(items)}")
    if not items:
        print("[!] Карточек нет — другая вёрстка или отдана заглушка/капча.")
        return soup, []

    print("\n--- какие классы вообще встречаются в карточках ---")
    classes = Counter()
    for it in items[:40]:
        for el in it.find_all(True):
            for c in el.get("class", []):
                classes[c] += 1
    for c, n in classes.most_common(20):
        print(f"  {c:24s} {n}")

    print("\n--- первые 3 карточки целиком ---")
    for i, it in enumerate(items[:3], 1):
        print(f"\n  === карточка {i} ===")
        print("  href :", it.get("href"))
        datas = {k: v for k, v in it.attrs.items() if k.startswith("data-")}
        print("  data-атрибуты карточки:", datas or "нет")
        for el in it.find_all(True):
            d = {k: v for k, v in el.attrs.items() if k.startswith("data-")}
            if d:
                print("    вложенный", el.get("class"), "->", d)
        txt = it.get_text(" ", strip=True)
        print("  текст:", repr(txt[:220]))
        m = re.search(r"(\d[\d\s]+)\s*₽", txt.lower())
        print("  старая регулярка цены ->", m.group(1).replace(" ", "") if m else None)

    print("\n--- сколько карточек содержат слово ранга (в тексте) ---")
    joined = [it.get_text(" ", strip=True).lower() for it in items]
    for w in RANK_WORDS:
        n = sum(1 for t in joined if w in t)
        if n:
            print(f"  {w:14s} {n:4d} из {len(items)}")

    ids = []
    for it in items:
        href = it.get("href") or ""
        m = re.search(r"id=(\d+)", href) or re.search(r"(\d{5,})", href)
        if m:
            ids.append(m.group(1))
    print(f"\nlot_id извлечено: {len(ids)}; пример: {ids[:5]}")
    return soup, ids


# ---------------------------------------------------------------- 2. фильтры
def probe_filters(soup):
    head("2. ФИЛЬТРЫ КАТЕГОРИИ (можно ли брать ранг из URL, а не из текста)")
    if soup is None:
        print("пропущено")
        return
    for sel in ["select", "input[type=checkbox]", "input[type=hidden]", "[data-filter]"]:
        els = soup.select(sel)
        if not els:
            continue
        print(f"\n--- {sel} ({len(els)}) ---")
        for el in els[:25]:
            name = el.get("name") or el.get("id") or el.get("data-filter")
            if el.name == "select":
                opts = [(o.get("value"), o.get_text(strip=True)) for o in el.find_all("option")[:12]]
                print(f"  name={name!r} options={opts}")
            else:
                print(f"  name={name!r} value={el.get('value')!r}")


# ------------------------------------------- 3. живёт ли лот вне листинга
def probe_offer_pages(session, active_ids):
    head("3. ГЛАВНОЕ: страница лота после исчезновения из листинга")

    gone_ids = []
    try:
        import pandas as pd
        d = pd.read_csv(CSV_FILE)
        d["last_seen"] = pd.to_datetime(d["last_seen"], errors="coerce")
        gone = d[d["sold"] == True].sort_values("last_seen")          # noqa: E712
        gone_ids = [
            str(x) for x in
            list(gone["lot_id"].head(3)) + list(gone["lot_id"].tail(3))
        ]
        print(f"из {CSV_FILE} взято {len(gone_ids)} давно исчезнувших лотов")
    except Exception as exc:
        print(f"[warn] не смог прочитать {CSV_FILE}: {exc}")

    print("\n--- лоты, которые ПРЯМО СЕЙЧАС в листинге (контроль) ---")
    for lot_id in active_ids[:3]:
        check_offer(session, lot_id)

    print("\n--- лоты, исчезнувшие из листинга давно ---")
    for lot_id in gone_ids:
        check_offer(session, lot_id)

    print("\nЧто хотим увидеть: активные -> 200 и на странице видно предложение;")
    print("исчезнувшие -> либо 404/редирект (значит лот снят и это надёжный детектор),")
    print("либо тоже 200 (значит лот жив, просто продавец офлайн — тогда детектор не работает).")


def check_offer(session, lot_id):
    url = OFFER_URL.format(lot_id)
    try:
        r = get(session, url)
    except requests.RequestException as exc:
        print(f"  {lot_id:>10}  ОШИБКА {exc}")
        return
    soup = BeautifulSoup(r.text, "lxml")
    title = soup.find("title")
    has_price = bool(soup.select_one(".tc-price, [data-s], .payment-value"))
    body = soup.get_text(" ", strip=True).lower()
    dead = any(w in body for w in ["не найдено", "not found", "удален", "удалён", "недоступно"])
    print(f"  {lot_id:>10}  HTTP {r.status_code}  redirect->{r.url != url}  "
          f"цена_на_странице={has_price}  признак_удаления={dead}  "
          f"title={title.get_text(strip=True)[:60] if title else None!r}")


# ---------------------------------------------------------------- 4. продавец
def probe_seller(session, soup):
    head("4. СТРАНИЦА ПРОДАВЦА (видны ли отзывы с категорией / суммой / датой)")
    if soup is None:
        print("пропущено")
        return
    link = soup.select_one("a[href*='/users/']")
    if not link:
        print("ссылок на продавцов в листинге не нашёл")
        return
    url = link.get("href")
    if not url.startswith("http"):
        url = "https://funpay.com" + url
    print("проверяю:", url)
    r = get(session, url)
    print("HTTP:", r.status_code)
    if r.status_code != 200:
        return
    with open("funpay_seller.html", "w", encoding="utf-8") as f:
        f.write(r.text)
    print("HTML сохранён: funpay_seller.html")

    s = BeautifulSoup(r.text, "lxml")
    for sel in [".review-container", ".review-item", ".dyn-table-body",
                ".review-item-date", ".review-item-detail", ".rating-value"]:
        els = s.select(sel)
        print(f"  {sel:24s} {len(els)}")
        if els:
            print("     пример:", repr(els[0].get_text(' ', strip=True)[:160]))


def main():
    with requests.Session() as session:
        soup, ids = probe_listing(session)
        probe_filters(soup)
        probe_offer_pages(session, ids)
        probe_seller(session, soup)
    print("\nГотово. Пришли весь вывод + funpay_%s.html, если он не пустой." % CAT)


if __name__ == "__main__":
    main()
