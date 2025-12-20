# src/scripts/ig_test_activity.py
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
            "  PYTHONPATH=src python src/scripts/ig_test_activity.py --env LIVE --force-live"
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


def _extract_id_like_columns(cols: List[str]) -> List[str]:
    keys = []
    for c in cols:
        s = c.lower()
        if ("deal" in s) or ("ref" in s) or ("level" in s) or ("type" in s) or ("status" in s) or ("action" in s):
            keys.append(c)
    return keys


def _extract_activity_candidates_flat(df: Any, deal_id: str) -> Any:
    """
    trading-ig often returns a flattened DF for activity when detailed=True
    with columns:
      - dealId
      - affectedDealId
      - actionType
      - level, stopLevel, limitLevel ...
    So we match via (dealId == deal_id) OR (affectedDealId == deal_id).
    """
    if df is None or len(df) == 0 or not deal_id:
        return df.iloc[0:0]

    deal_id_s = str(deal_id)

    cols = {c.lower(): c for c in df.columns}
    deal_col = cols.get("dealid")
    aff_col = cols.get("affecteddealid")

    if not deal_col and not aff_col:
        # fallback: try any column containing deal + id
        for c in df.columns:
            cl = c.lower()
            if "deal" in cl and "id" in cl and not deal_col:
                deal_col = c
            if "affected" in cl and "deal" in cl and "id" in cl and not aff_col:
                aff_col = c

    mask = None
    if deal_col:
        try:
            mask = (df[deal_col].astype(str) == deal_id_s)
        except Exception:
            pass

    if aff_col:
        try:
            m2 = (df[aff_col].astype(str) == deal_id_s)
            mask = m2 if mask is None else (mask | m2)
        except Exception:
            pass

    if mask is None:
        return df.iloc[0:0]

    return df[mask]


def _is_closeish_action_type(action_type: str) -> bool:
    s = (action_type or "").upper()
    return any(
        k in s
        for k in [
            "POSITION_CLOSED",
            "POSITION_PARTIALLY_CLOSED",
            "STOP_ORDER_FILLED",
            "LIMIT_ORDER_FILLED",
            "STOP_LIMIT",
            "LIMIT_ORDER",
            "STOP_ORDER",
        ]
    )


def _print_flat_row(row: Dict[str, Any], *, show_all_fields: bool = False) -> None:
    """
    Print one flattened activity row.
    """
    print("\n--- ACTIVITY ROW ---")
    if show_all_fields:
        # full dump (sorted keys for readability)
        for k in sorted(row.keys()):
            v = row.get(k)
            if v is not None and str(v) != "nan":
                print(f"{k}: {v}")
        return

    # compact useful subset
    keys = [
        "date", "type", "status", "channel", "epic", "marketName", "period",
        "dealId", "affectedDealId", "actionType",
        "direction", "size",
        "level", "stopLevel", "limitLevel", "stopDistance", "limitDistance",
        "description",
    ]
    for k in keys:
        if k in row:
            v = row.get(k)
            if v is not None and str(v) != "nan":
                print(f"{k}: {v}")


def main() -> None:
    # Always load trading-bot/.env regardless of working directory
    env_path = Path(__file__).resolve().parents[2] / ".env"  # trading-bot/.env
    load_dotenv(dotenv_path=env_path, override=False)

    p = argparse.ArgumentParser("IG test account activity (deal_id) + actionType/affectedDealId dump (flattened DF)")

    p.add_argument("--env", choices=["PRACTICE", "LIVE"], default=(os.getenv("IG_ENV") or "PRACTICE").upper())
    p.add_argument("--force-live", action="store_true", help="ALLOW LIVE (DANGEROUS)")

    # IMPORTANT: user wants this mandatory
    p.add_argument("--deal-id", required=True, help="Deal ID like DIAAAAV2...")

    p.add_argument("--lookback-hours", type=float, default=24.0)
    p.add_argument("--page-size", type=int, default=200)
    p.add_argument("--max-rows", type=int, default=200)

    # Always detailed so we get actionType/affectedDealId columns
    p.add_argument("--dump-rows", action="store_true", help="Print matching activity rows")
    p.add_argument("--only-closeish-actions", action="store_true", help="Only show rows whose actionType looks like close/TP/SL")
    p.add_argument("--show-all-fields", action="store_true", help="Dump every non-empty field for each row")

    # KEY FIX: do not pass dealId to IG API (otherwise you may miss close events that only reference affectedDealId)
    p.add_argument(
        "--no-api-deal-filter",
        action="store_true",
        help="Fetch activity WITHOUT dealId=... (server filter). Required to catch close events where your deal id appears only as affectedDealId.",
    )

    args = p.parse_args()

    ig = _connect_ig(args.env, args.force_live)

    from datetime import datetime, timedelta

    to_date = datetime.utcnow()
    from_date = to_date - timedelta(hours=float(args.lookback_hours))

    detailed = True  # we need actionType/affectedDealId columns

    print("\n--- FETCH ACCOUNT ACTIVITY ---")
    print("deal_id (local filter):", args.deal_id)
    print("no_api_deal_filter:", bool(args.no_api_deal_filter))
    print("from_date (UTC):", from_date.isoformat())
    print("to_date   (UTC):", to_date.isoformat())
    print("detailed:", detailed)
    print("page_size:", args.page_size)

    api_deal_id = None if args.no_api_deal_filter else args.deal_id

    act = _with_backoff(lambda: ig.fetch_account_activity(
        from_date=from_date,
        to_date=to_date,
        detailed=detailed,
        deal_id=api_deal_id,   # IMPORTANT: can be None
        page_size=args.page_size,
    ))

    print("\nReturn type:", type(act))

    if not _is_dataframe(act):
        print("\n[ERROR] Unexpected structure from fetch_account_activity()")
        print(act)
        raise SystemExit(1)

    print("\nColumns:", list(act.columns))

    id_cols = _extract_id_like_columns(list(act.columns))
    print("\nID/LEVEL/TYPE columns detected:", id_cols)

    if id_cols:
        print("\nSample (id-like cols):")
        print(act[id_cols].head(10).to_string(index=False))

    # Match locally via dealId OR affectedDealId (flattened DF)
    hits = _extract_activity_candidates_flat(act, args.deal_id)
    print(f"\nMatched rows where dealId==deal_id OR affectedDealId==deal_id: {len(hits)}")

    if len(hits) == 0:
        print("\n[INFO] No matching rows found.")
        print("Try:")
        print("  - increasing --lookback-hours")
        print("  - using --no-api-deal-filter (recommended)")
        print("  - increasing --page-size (max 500)")
        return

    # Optional: only close-ish by actionType
    if args.only_closeish_actions and "actionType" in hits.columns:
        before = len(hits)
        hits = hits[hits["actionType"].astype(str).apply(lambda s: _is_closeish_action_type(str(s)))]
        print(f"Filtered close-ish actionType rows: {len(hits)} (from {before})")

    # Sort newest-first by 'date' if available
    if "date" in hits.columns:
        hits = hits.sort_values(by="date", ascending=False)

    if args.dump_rows:
        print("\n=== MATCHING ACTIVITY ROWS ===")
        for _, r in hits.head(args.max_rows).iterrows():
            _print_flat_row(r.to_dict(), show_all_fields=bool(args.show_all_fields))
        print("\n=== END ===")
    else:
        # Default: compact table view
        view_cols = [c for c in ["date", "type", "status", "dealId", "affectedDealId", "actionType", "level", "stopLevel", "limitLevel", "description"] if c in hits.columns]
        print("\n--- SUMMARY (matching rows) ---")
        print(hits[view_cols].head(min(args.max_rows, 30)).to_string(index=False))

    print("\nDone.")


if __name__ == "__main__":
    main()