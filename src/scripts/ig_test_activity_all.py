# src/scripts/ig_test_activity.py
import os
import time
import json
import argparse
from pathlib import Path
from typing import Any, Dict, Optional, List

from dotenv import load_dotenv
from trading_ig.rest import IGService, ApiExceededException
from urllib.parse import urlparse, parse_qs


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _with_backoff(fn, *, max_retries: int = 6, base_sleep: float = 0.8):
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except ApiExceededException:
            sleep_s = base_sleep * (2 ** (attempt - 1))
            print(f"[WARN] IG rate limit hit -> sleeping {sleep_s:.1f}s")
            time.sleep(sleep_s)
    raise ApiExceededException()


def _connect_ig(env: str, force_live: bool) -> IGService:
    if env == "LIVE" and not force_live:
        raise SystemExit("Refusing to run on LIVE (use --force-live).")

    username = os.getenv("IG_SERVICE_USERNAME") or os.getenv("IG_USER")
    password = os.getenv("IG_SERVICE_PASSWORD") or os.getenv("IG_PASS")
    api_key = os.getenv("IG_SERVICE_API_KEY")
    if not username or not password or not api_key:
        raise SystemExit("Missing IG credentials in .env")

    acc_type = "DEMO" if env == "PRACTICE" else "LIVE"
    print(f"[INFO] Connecting to IG acc_type={acc_type}")
    ig = IGService(username, password, api_key, acc_type=acc_type)
    _with_backoff(lambda: ig.create_session())
    return ig


def _json_pp(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


# ──────────────────────────────────────────────────────────────────────────────
# RAW activity fetch (preserves details.actions)
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_account_activity_raw(
    ig: IGService,
    *,
    from_date,
    to_date,
    detailed: bool,
    page_size: int,
    deal_id_api_filter: Optional[str],
) -> List[Dict[str, Any]]:

    params = {
        "from": from_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "to": to_date.strftime("%Y-%m-%dT%H:%M:%S"),
        "pageSize": int(page_size),
    }
    if detailed:
        params["detailed"] = "true"
    if deal_id_api_filter:
        params["dealId"] = deal_id_api_filter

    endpoint = "/history/activity/"
    action = "read"
    version = "3"

    activities: List[Dict[str, Any]] = []
    more_results = True

    while more_results:
        resp = _with_backoff(lambda: ig._req(action, endpoint, params, None, version))
        data = ig.parse_response(resp.text)

        acts = data.get("activities") or []
        activities.extend([a for a in acts if isinstance(a, dict)])

        paging = (data.get("metadata") or {}).get("paging") or {}
        nxt = paging.get("next")

        if not nxt:
            more_results = False
        else:
            q = parse_qs(urlparse(nxt).query)
            params["from"] = q.get("from", [params.get("from")])[0]
            params["to"] = q.get("to", [params.get("to")])[0]

    return activities


def _matches_deal_raw(activity: Dict[str, Any], deal_id: str) -> bool:
    deal_id = str(deal_id)

    if str(activity.get("dealId") or "") == deal_id:
        return True

    details = activity.get("details")
    if isinstance(details, dict):
        actions = details.get("actions")
        if isinstance(actions, list):
            return any(str(a.get("affectedDealId")) == deal_id for a in actions if isinstance(a, dict))

    return False


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path)

    p = argparse.ArgumentParser("IG RAW activity dump")
    p.add_argument("--env", choices=["PRACTICE", "LIVE"], default="PRACTICE")
    p.add_argument("--force-live", action="store_true")

    p.add_argument("--deal-id", required=True)
    p.add_argument("--lookback-hours", type=float, default=168)
    p.add_argument("--page-size", type=int, default=500)
    p.add_argument("--no-api-deal-filter", action="store_true")

    p.add_argument("--raw", action="store_true")
    p.add_argument("--raw-save-all", type=str, help="Save ALL raw activities to JSON")

    args = p.parse_args()

    ig = _connect_ig(args.env, args.force_live)

    from datetime import datetime, timedelta
    to_date = datetime.utcnow()
    from_date = to_date - timedelta(hours=args.lookback_hours)

    if args.raw:
        acts = _fetch_account_activity_raw(
            ig,
            from_date=from_date,
            to_date=to_date,
            detailed=True,
            page_size=args.page_size,
            deal_id_api_filter=None if args.no_api_deal_filter else args.deal_id,
        )

        matched = [a for a in acts if _matches_deal_raw(a, args.deal_id)]

        print(f"\nRaw activities fetched: {len(acts)}")
        print(f"Matched activities for deal_id={args.deal_id}: {len(matched)}")

        if args.raw_save_all:
            payload = {
                "all_activities": acts,
                "matched_activities": matched,
            }
            with open(args.raw_save_all, "w", encoding="utf-8") as f:
                f.write(_json_pp(payload))
            print(f"[OK] Saved ALL raw activities to: {args.raw_save_all}")

        return

    print("Nothing to do (run with --raw).")


if __name__ == "__main__":
    main()