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


def _extract_activity_candidates(df: Any, deal_id: str) -> Optional[Tuple[Any, str]]:
    """
    Try to find activity rows related to a given deal_id.
    We check common column names used by trading-ig formatting.
    """
    if df is None or len(df) == 0 or not deal_id:
        return None

    candidate_cols = [c for c in df.columns if c.lower() in ("dealid", "affecteddealid", "deal_id", "affected_deal_id")]
    if not candidate_cols:
        candidate_cols = [c for c in df.columns if ("deal" in c.lower() and "id" in c.lower())]

    if not candidate_cols:
        return None

    for col in candidate_cols:
        try:
            hits = df[df[col].astype(str) == str(deal_id)]
            if len(hits) > 0:
                return hits, col
        except Exception:
            continue

    return None


def _guess_execution_level_from_activity_row(row: Dict[str, Any]) -> Optional[float]:
    """
    In IG activity, execution level often appears as:
      - 'level'
      - 'details.level'
    trading-ig formatted DF often flattens nested details into columns like:
      - level, stopLevel, limitLevel, ...
    Note: For POSITION_OPENED, 'level' == entry. For close events, 'level' may be close execution price.
    """
    for key in ["level", "details.level", "details_level"]:
        if key in row and row[key] is not None:
            try:
                return float(row[key])
            except Exception:
                pass
    return None


def _guess_reason_from_activity_row(row: Dict[str, Any]) -> str:
    """
    Guess close reason from actionType/type/description when possible.
    """
    t = str(row.get("actionType") or row.get("action_type") or "").upper()
    typ = str(row.get("type") or "").upper()
    desc = str(row.get("description") or "").upper()
    blob = " ".join([t, typ, desc])

    if "LIMIT_ORDER_FILLED" in blob:
        return "TP/LIMIT_FILLED"
    if "STOP_ORDER_FILLED" in blob:
        return "SL/STOP_FILLED"
    if "POSITION_CLOSED" in blob:
        return "POSITION_CLOSED"
    if "POSITION_PARTIALLY_CLOSED" in blob:
        return "POSITION_PARTIALLY_CLOSED"
    if "POSITION_OPENED" in blob:
        return "POSITION_OPENED"
    return "UNKNOWN"


def _find_deal_reference_in_activity_row(row: Dict[str, Any], df_columns: Optional[List[str]] = None) -> Optional[str]:
    """
    trading-ig may flatten nested 'details.dealReference' into various column names.
    We check:
      - direct keys in row
      - any df columns containing 'dealReference' (case-insensitive)
      - common flattened variants: details.dealReference, details_dealReference, dealReference.1, etc.
    """
    # 1) direct keys (if row already contains it)
    direct_keys = [
        "dealReference",
        "deal_reference",
        "details.dealReference",
        "details_dealReference",
        "details.deal_reference",
        "details_deal_reference",
    ]
    for k in direct_keys:
        v = row.get(k)
        if v is not None and str(v).strip() != "":
            return str(v)

    # 2) if we have DF columns, scan for any column name that contains "dealreference"
    if df_columns:
        for c in df_columns:
            if "dealreference" in c.lower():
                v = row.get(c)
                if v is not None and str(v).strip() != "":
                    return str(v)

    return None


def _get_tx_closelevel_by_reference(
    ig: IGService,
    ref: str,
    lookback_hours: float,
    page_size: int = 200,
    max_pages: int = 6
) -> Optional[float]:
    from datetime import datetime, timedelta

    to_date = datetime.utcnow()
    from_date = to_date - timedelta(hours=float(lookback_hours))

    for page in range(1, max_pages + 1):
        tx = _with_backoff(lambda: ig.fetch_transaction_history(
            from_date=from_date,
            to_date=to_date,
            page_size=page_size,
            page_number=page,
        ))

        if not _is_dataframe(tx) or len(tx) == 0:
            continue

        if "reference" not in tx.columns:
            continue

        hits = tx[tx["reference"].astype(str) == str(ref)]
        if len(hits) == 0:
            continue

        if "dateUtc" in hits.columns:
            hits = hits.sort_values(by="dateUtc", ascending=False)

        if "closeLevel" in hits.columns:
            try:
                return float(hits.iloc[0]["closeLevel"])
            except Exception:
                return None

    return None


def _deep_scan_deal_reference(df: Any, max_rows: int = 50) -> None:
    """
    Thoroughly scans DataFrame for any dealReference signal:
      - columns containing 'ref'
      - columns containing 'dealReference' (case-insensitive)
      - if a 'details' column exists and stores dict/json-like objects, tries to extract details['dealReference']
    """
    print("\n=== DEEP SCAN: dealReference ===")

    # 1) columns containing 'ref'
    ref_cols = [c for c in df.columns if "ref" in c.lower()]
    print("Columns containing 'ref':", ref_cols)

    for c in ref_cols:
        try:
            s = df[c]
            non_empty = df[~s.isna() & (s.astype(str).str.strip() != "")]
            print(f"\n-- Column '{c}' non-empty count:", len(non_empty))
            if len(non_empty) > 0:
                print(non_empty[[c]].head(max_rows).to_string(index=False))
        except Exception as e:
            print(f"[WARN] Could not inspect column '{c}': {e}")

    # 2) columns containing 'dealReference'
    dealref_cols = [c for c in df.columns if "dealreference" in c.lower()]
    print("\nColumns containing 'dealReference' (any casing):", dealref_cols)

    for c in dealref_cols:
        try:
            s = df[c]
            non_empty = df[~s.isna() & (s.astype(str).str.strip() != "")]
            print(f"\n-- Column '{c}' non-empty count:", len(non_empty))
            if len(non_empty) > 0:
                print(non_empty[[c]].head(max_rows).to_string(index=False))
        except Exception as e:
            print(f"[WARN] Could not inspect column '{c}': {e}")

    # 3) if there is a 'details' column, try extracting dealReference from dicts
    if "details" in df.columns:
        print("\nFound 'details' column. Scanning first rows for details['dealReference'] / actions[*] ...")
        hits: List[Tuple[int, str]] = []
        for idx, v in df["details"].head(max_rows).items():
            if isinstance(v, dict):
                dr = v.get("dealReference")
                if dr:
                    hits.append((idx, str(dr)))
                actions = v.get("actions")
                if isinstance(actions, list):
                    for a in actions:
                        if isinstance(a, dict) and a.get("dealReference"):
                            hits.append((idx, str(a.get("dealReference"))))
            elif isinstance(v, str) and "dealReference" in v:
                hits.append((idx, "[string_contains_dealReference]"))
        print("details-derived hits:", hits[:20] if hits else "None")

    print("\n=== END DEEP SCAN ===")


def main() -> None:
    # Always load trading-bot/.env regardless of working directory
    env_path = Path(__file__).resolve().parents[2] / ".env"  # trading-bot/.env
    load_dotenv(dotenv_path=env_path, override=False)

    p = argparse.ArgumentParser("IG test account activity (deal_id) + dealReference deep scan")
    p.add_argument("--env", choices=["PRACTICE", "LIVE"], default=(os.getenv("IG_ENV") or "PRACTICE").upper())
    p.add_argument("--force-live", action="store_true", help="ALLOW LIVE (DANGEROUS)")

    p.add_argument("--deal-id", required=True, help="Deal ID like DIAAAAV2...")
    p.add_argument("--lookback-hours", type=float, default=24.0)
    p.add_argument("--page-size", type=int, default=200)
    p.add_argument("--max-rows", type=int, default=30)
    p.add_argument("--detailed", action="store_true", help="Call activity with detailed=True")
    p.add_argument("--cross-history", action="store_true", help="If dealReference found, lookup closeLevel in transaction history")
    p.add_argument("--deep-scan", action="store_true", help="Do a thorough scan for dealReference in DF")
    args = p.parse_args()

    ig = _connect_ig(args.env, args.force_live)

    from datetime import datetime, timedelta

    to_date = datetime.utcnow()
    from_date = to_date - timedelta(hours=float(args.lookback_hours))

    print("\n--- FETCH ACCOUNT ACTIVITY ---")
    print("deal_id:", args.deal_id)
    print("from_date (UTC):", from_date.isoformat())
    print("to_date   (UTC):", to_date.isoformat())
    print("detailed:", bool(args.detailed))
    print("page_size:", args.page_size)

    act = _with_backoff(lambda: ig.fetch_account_activity(
        from_date=from_date,
        to_date=to_date,
        detailed=bool(args.detailed),
        deal_id=args.deal_id,
        page_size=args.page_size,
    ))

    print("\nReturn type:", type(act))

    if not _is_dataframe(act):
        print("\n[ERROR] Unexpected structure from fetch_account_activity()")
        print(act)
        raise SystemExit(1)

    id_cols = _extract_id_like_columns(list(act.columns))
    print("\nID/LEVEL/TYPE columns detected:", id_cols)

    if id_cols:
        print("\nSample (id-like cols):")
        print(act[id_cols].head(args.max_rows).to_string(index=False))

    if args.deep_scan:
        _deep_scan_deal_reference(act, max_rows=max(args.max_rows, 50))

    res = _extract_activity_candidates(act, args.deal_id)
    if res is None:
        print(f"\n[INFO] No rows matched deal_id={args.deal_id} via dealId/affectedDealId columns.")
        print("Try increasing --lookback-hours and --page-size, or run with --deep-scan to inspect ref fields.")
        print("\nAll columns:")
        print(list(act.columns))
        print("\nHead:")
        print(act.head(args.max_rows).to_string(index=False))
        return

    hits, col = res
    print(f"\n✅ Matched {len(hits)} row(s) by column '{col}' == deal_id")

    # pick newest row by best available timestamp column
    sort_col = None
    for c in ["dateUtc", "date", "timestamp", "createdDateUtc", "createdDate"]:
        if c in hits.columns:
            sort_col = c
            break
    if sort_col:
        hits = hits.sort_values(by=sort_col, ascending=False)

    top = hits.iloc[0].to_dict()
    exec_level = _guess_execution_level_from_activity_row(top)
    reason = _guess_reason_from_activity_row(top)
    deal_ref = _find_deal_reference_in_activity_row(top, df_columns=list(hits.columns))

    print("\n--- BEST GUESS (latest matching activity row) ---")
    print("execution_level_from_activity:", exec_level)
    print("actionType/type reason guess:", reason)
    print("dealReference (if any):", deal_ref)

    if args.cross_history and deal_ref:
        tx_close = _get_tx_closelevel_by_reference(ig, deal_ref, lookback_hours=args.lookback_hours)
        print("\n--- CROSS CHECK (transaction history by reference == dealReference) ---")
        print("tx_closeLevel:", tx_close)

    print("\nDone.")


if __name__ == "__main__":
    main()
