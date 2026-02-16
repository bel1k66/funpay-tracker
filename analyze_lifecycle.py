import pandas as pd

LIFECYCLE_FILE = "lot_lifecycle.csv"


def load_data() -> pd.DataFrame:
    life = pd.read_csv(LIFECYCLE_FILE)

    for col in ["first_seen", "last_seen"]:
        if col in life.columns:
            life[col] = pd.to_datetime(life[col], errors="coerce")

    life["sold"] = life.get("sold", False).fillna(False).astype(bool)
    life["winrate"] = pd.to_numeric(life.get("winrate"), errors="coerce")
    life["sold_duration_h"] = pd.to_numeric(life.get("sold_duration_h"), errors="coerce")
    if "winrate_status" not in life.columns:
        life["winrate_status"] = "missing"

    life["sold_duration_h"] = life["sold_duration_h"].fillna(
        (life["last_seen"] - life["first_seen"]).dt.total_seconds() / 3600
    )

    return life


def print_summary(life: pd.DataFrame) -> None:
    total = len(life)
    active = (~life["sold"]).sum()
    sold = life["sold"].sum()
    wr_known = life["winrate"].notna().sum()
    wr_zero = (life["winrate_status"] == "zero_reported").sum()

    print("=== ОБЩАЯ СВОДКА ===")
    print(f"Всего лотов в истории: {total}")
    print(f"Активные: {active}")
    print(f"Проданные: {sold}")
    print(f"Winrate заполнен: {wr_known} ({(wr_known / total * 100) if total else 0:.1f}%)")
    print(f"Winrate = 0% (особый кейс): {wr_zero}")


def print_rank_report(life: pd.DataFrame) -> None:
    print("\n=== ОТЧЁТ ПО RANK_DIV ===")

    report_rows = []
    for rank_div, part in life.groupby("rank_div", dropna=True):
        sold_part = part[part["sold"]]

        report_rows.append(
            {
                "rank_div": rank_div,
                "lots_total": len(part),
                "lots_sold": len(sold_part),
                "active": (~part["sold"]).sum(),
                "median_price": round(part["price"].median(), 2) if part["price"].notna().any() else None,
                "median_sale_hours": round(sold_part["sold_duration_h"].median(), 2)
                if sold_part["sold_duration_h"].notna().any()
                else None,
                "median_wr": round(part["winrate"].median(), 2) if part["winrate"].notna().any() else None,
                "wr_coverage_pct": round((part["winrate"].notna().mean() * 100), 1),
                "wr_zero_special": int((part["winrate_status"] == "zero_reported").sum()),
            }
        )

    if not report_rows:
        print("Данных по rank_div пока нет.")
        return

    report = pd.DataFrame(report_rows).sort_values(by=["rank_div"]).reset_index(drop=True)
    print(report.to_string(index=False))


def main() -> None:
    life = load_data()
    print_summary(life)
    print_rank_report(life)


if __name__ == "__main__":
    main()
