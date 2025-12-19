# src/scripts/ig_test_history.py
import os
import time
import argparse
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

from dotenv import load_dotenv
from trading_ig.rest import IGService, ApiExceededException


def _with_backoff(fn, *, max_retries: int = 6, base_sleep: float = 0.8):
    """Retry IG calls on rate limit with exponential backoff."""
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except ApiExceededException:
            sleep_s = base_sleep * (2 ** (attempt - 1))
            print(f"[WARN] IG rate limit hit -> sleeping {sleep_s:.1f}s (attempt {attempt}/{max_retries})")
            time.sleep(sleep_s)
    raise ApiExceededException()


def _connect_ig(env: str, force_live: bool) -> IGService:
    if env == "LIVE" and not force_live:
        raise SystemExit(
            "Refusing to run on LIVE.\n"
            "If you REALLY want LIVE, run:\n"
            "  PYTHONPATH=src python src/scripts/ig_test_history.py --env LIVE --force-live"
        )

    username = os.getenv("IG_SERVICE_USERNAME") or os.getenv("IG_USER")
    password = os.getenv("IG_SERVICE_PASSWORD") or os.getenv("IG_PASS")
    api_key = os.getenv("IG_SERVICE_API_KEY")
    if not username or not password or not api_key:
        raise SystemExit("Missing IG creds in .env (IG_SERVICE_USERNAME, IG_SERVICE_PASSWORD, IG_SERVICE_API_KEY)")

    acc_type = "DEMO" if env == "PRACTICE" else "LIVE"
    print(f"[INFO] Connecting to IG acc_type={acc_type}")
    ig = IGService(username=username, password=password, api_key=api_key, acc_type=acc_type)
    _with_backoff(lambda: ig.create_session())
    return ig


def _is_dataframe(x: Any) -> bool:
    return x.__class__.__name__ == "DataFrame"


def _print_df_overview(df: Any, max_rows: int = 10) -> None:
    # df is expected to be a pandas DataFrame from trading-ig
    print("\n--- TX: DATAFRAME OVERVIEW ---")
    print("rows:", len(df))
    print("columns:", list(df.columns))

    cols_show = [c for c in ["dateUtc", "date", "transactionType", "reference", "openLevel", "closeLevel",
                             "instrumentName", "instrumentName.1", "profitAndLoss"] if c in df.columns]
    if cols_show:
        print("\nHead (selected cols):")
        print(df[cols_show].head(max_rows).to_string(index=False))
    else:
        print("\nHead:")
        print(df.head(max_rows).to_string(index=False))


def _print_dict_overview(d: Dict[str, Any], max_rows: int = 10) -> None:
    txs = d.get("transactions", []) or []
    print("\n--- TX: DICT OVERVIEW ---")
    print("transactions:", len(txs))
    if txs:
        print("keys in first tx:", list(txs[0].keys()))
        print("\nFirst tx (truncated):")
        # print a small subset if present
        keys = ["dateUtc", "date", "transactionType", "reference", "openLevel", "closeLevel", "profitAndLoss"]
        out = {k: txs[0].get(k) for k in keys if k in txs[0]}
        print(out)
        print("\nFirst few references:")
        for t in txs[:max_rows]:
            print("-", t.get("reference"), t.get("transactionType"), t.get("closeLevel"))


def _match_deal_in_df(df: Any, deal_id: str) -> Any:
    """
    Try to find matching rows for a deal_id.
    Primary guess: df['reference'] == deal_id.
    Also tries other plausible columns.
    """
    if not deal_id:
        return None

    candidates = []
    for col in ["reference", "dealId", "dealID", "dealReference", "deal_reference"]:
        if col in df.columns:
            candidates.append(col)

    if not candidates:
        return None

    for col in candidates:
        try:
            hits = df[df[col].astype(str) == str(deal_id)]
            if len(hits) > 0:
                return hits, col
        except Exception:
            continue

    return None


def _match_deal_in_dict(d: Dict[str, Any], deal_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
    if not deal_id:
        return None
    txs = d.get("transactions", []) or []
    keys = ["reference", "dealId", "dealID", "dealReference", "deal_reference"]
    for t in txs:
        for k in keys:
            if str(t.get(k, "")) == str(deal_id):
                return t, k
    return None


def main() -> None:
    # Always load trading-bot/.env regardless of working directory (same style as your script)
    env_path = Path(__file__).resolve().parents[2] / ".env"  # trading-bot/.env
    load_dotenv(dotenv_path=env_path, override=False)

    p = argparse.ArgumentParser("IG test transaction history + deal_id match")
    p.add_argument("--env", choices=["PRACTICE", "LIVE"], default=(os.getenv("IG_ENV") or "PRACTICE").upper())
    p.add_argument("--force-live", action="store_true", help="ALLOW LIVE (DANGEROUS)")

    p.add_argument("--lookback-hours", type=float, default=6.0, help="How many hours back to query")
    p.add_argument("--page-size", type=int, default=50)
    p.add_argument("--page", type=int, default=1, help="Which page to request (pagination)")
    p.add_argument("--max-rows", type=int, default=10, help="How many rows to print")

    p.add_argument("--deal-id", default="", help="Optional: deal_id to search for and print matching closeLevel")
    p.add_argument("--show-raw", action="store_true", help="Print raw structure (can be large)")

    args = p.parse_args()

    ig = _connect_ig(args.env, args.force_live)

    from datetime import datetime, timedelta

    to_date = datetime.utcnow()
    from_date = to_date - timedelta(hours=float(args.lookback_hours))

    print("\n--- FETCH TRANSACTION HISTORY ---")
    print("from_date (UTC):", from_date.isoformat())
    print("to_date   (UTC):", to_date.isoformat())
    print("page_size:", args.page_size, "page_number:", args.page)

    tx = _with_backoff(
        lambda: ig.fetch_transaction_history(
            from_date=from_date,
            to_date=to_date,
            page_size=args.page_size,
            page_number=args.page,
        )
    )

    print("\nReturn type:", type(tx))

    if args.show_raw:
        print("\n--- RAW ---")
        print(tx)

    # Overview
    if _is_dataframe(tx):
        _print_df_overview(tx, max_rows=args.max_rows)

        # Optional deal_id match
        if args.deal_id:
            res = _match_deal_in_df(tx, args.deal_id)
            if res is None:
                print(f"\n[INFO] No match for deal_id={args.deal_id} on page {args.page}.")
                print("Try a larger --lookback-hours or a different --page.")
            else:
                hits, col = res
                print(f"\n✅ Found {len(hits)} matching row(s) by column '{col}'")
                # print most relevant columns
                cols = [c for c in ["dateUtc", "transactionType", col, "openLevel", "closeLevel", "profitAndLoss"] if c in hits.columns]
                print(hits[cols].head(10).to_string(index=False))

                # show extracted closeLevel
                if "closeLevel" in hits.columns:
                    close_level = hits.iloc[0]["closeLevel"]
                    print("\ncloseLevel:", close_level)
                else:
                    print("\n[WARN] 'closeLevel' not present in matched rows.")
    elif isinstance(tx, dict):
        _print_dict_overview(tx, max_rows=args.max_rows)

        if args.deal_id:
            m = _match_deal_in_dict(tx, args.deal_id)
            if not m:
                print(f"\n[INFO] No match for deal_id={args.deal_id} on page {args.page}.")
                print("Try a larger --lookback-hours or a different --page.")
            else:
                hit, key = m
                print(f"\n✅ Found matching tx by key '{key}'")
                print("closeLevel:", hit.get("closeLevel"))
                print("transactionType:", hit.get("transactionType"))
                print("reference:", hit.get("reference"))
    else:
        print("\n[ERROR] Unexpected return structure from fetch_transaction_history().")
        print("Value:", tx)
        raise SystemExit(1)

    print("\nDone.")


if __name__ == "__main__":
    main()