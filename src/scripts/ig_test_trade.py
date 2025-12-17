# src/scripts/ig_test_trade.py
import os
import time
import argparse
import inspect
from pathlib import Path
from typing import Any, Dict, Optional, List

from dotenv import load_dotenv
from trading_ig.rest import IGService, ApiExceededException


def _confirm_by_ref(ig: IGService, deal_ref: str):
    if hasattr(ig, "fetch_deal_by_deal_reference"):
        return ig.fetch_deal_by_deal_reference(deal_ref)
    if hasattr(ig, "fetch_deal_confirmation"):
        return ig.fetch_deal_confirmation(deal_ref)
    return None


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
            "  PYTHONPATH=src python src/scripts/ig_test_trade.py --env LIVE --force-live"
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


def _market_check(ig: IGService, epic: str) -> Dict[str, Any]:
    m = _with_backoff(lambda: ig.fetch_market_by_epic(epic))

    instrument = m.get("instrument", {}) or {}
    snapshot = m.get("snapshot", {}) or {}
    rules = m.get("dealingRules", {}) or {}

    instrument_name = instrument.get("name") or instrument.get("instrumentName")
    expiry = instrument.get("expiry")  # IMPORTANT
    market_status = snapshot.get("marketStatus")
    bid = snapshot.get("bid")
    offer = snapshot.get("offer")

    min_deal = (rules.get("minDealSize") or {}).get("value")
    min_step = (rules.get("minStepDistance") or {}).get("value")

    print("\n--- MARKET CHECK ---")
    print("EPIC:", epic)
    print("instrument.name:", instrument_name)
    print("instrument.expiry:", expiry)
    print("marketStatus:", market_status)
    print("bid / offer:", bid, "/", offer)
    print("minDealSize:", min_deal)
    print("minStepDistance:", min_step)

    if market_status != "TRADEABLE":
        raise SystemExit(f"Market is not TRADEABLE (status={market_status})")

    if bid is None or offer is None:
        raise SystemExit("Missing bid/offer in snapshot")

    # If IG doesn't return expiry, rolling markets commonly use DFB.
    expiry = expiry or "DFB"

    return {
        "raw": m,
        "instrument_name": instrument_name,
        "expiry": expiry,
        "bid": float(bid),
        "offer": float(offer),
        "min_deal": float(min_deal) if min_deal is not None else None,
        "min_step": float(min_step) if min_step is not None else None,
    }


def _flag_spot_like(name: str, expiry: str) -> str:
    s = (name or "").lower()
    e = (expiry or "").upper()

    if e in ("DFB", "-"):
        if ("cfd" in s) or ("spot" in s) or ("rolling" in s) or ("daily funded" in s) or ("dfb" in s):
            return "✅ SPOT/CFD-like"
        return "⚠️ rolling-ish"

    if e and e != "-":
        return "🟠 FUTURE/EXPIRY"

    return ""


def _discover_epics(ig: IGService, query: str, limit: int = 25) -> List[Dict[str, Any]]:
    """
    Lists candidate markets from search_markets(query) and prints their expiry by calling fetch_market_by_epic on each.
    Try queries: "gold", "xauusd", "spot gold", "gc".
    """
    print(f"\n--- DISCOVER: search_markets('{query}') ---")
    results = _with_backoff(lambda: ig.search_markets(query))

    # trading-ig usually returns a pandas DataFrame
    try:
        df = results
        if "marketStatus" in df.columns:
            df = df[df["marketStatus"] == "TRADEABLE"]
        df = df.head(limit)
        epics = list(df["epic"].values)
        names = list(df["instrumentName"].values)
    except Exception:
        raise SystemExit("search_markets(query) returned an unexpected structure in your trading-ig version.")

    out: List[Dict[str, Any]] = []
    for epic, name in zip(epics, names):
        try:
            m = _with_backoff(lambda e=epic: ig.fetch_market_by_epic(e))
            expiry = (m.get("instrument", {}) or {}).get("expiry") or ""
            out.append({"epic": epic, "name": name, "expiry": expiry})
        except Exception as e:
            out.append({"epic": epic, "name": name, "expiry": f"ERROR: {e}"})

    print("\nTRADEABLE candidates:")
    print("  (Look for expiry='DFB' or '-' + names containing 'CFD'/'Spot'/'Rolling')\n")
    for r in out:
        flag = _flag_spot_like(r["name"], r["expiry"])
        print(f"- {r['epic']} | {r['name']} | expiry={r['expiry']} {flag}")

    return out


def _build_kwargs_for_create_open_position(
    ig: IGService,
    currency: str,
    direction: str,
    epic: str,
    expiry: str,
    size: float,
    stop_distance: Optional[float],
    limit_distance: Optional[float],
    order_type: str,
    level: Optional[float],
    time_in_force: Optional[str],
) -> Dict[str, Any]:
    """
    Builds kwargs matching YOUR installed trading-ig signature (kwargs-only).
    Avoids mutual-exclusive validation by using *_distance and setting *_level to None.
    """
    sig = inspect.signature(ig.create_open_position)
    param_names = set(sig.parameters.keys())

    candidate = {
        "currency_code": currency,
        "direction": direction,
        "epic": epic,
        "expiry": expiry,
        "force_open": True,
        "guaranteed_stop": False,
        "order_type": order_type,
        "level": level,
        "time_in_force": time_in_force,
        "quote_id": None,
        "size": size,
        "trailing_stop": False,
        "trailing_stop_increment": None,
        "session": None,
        # distances
        "stop_distance": stop_distance,
        "limit_distance": limit_distance,
        # levels must be None if distances used
        "stop_level": None,
        "limit_level": None,
    }

    kwargs = {k: v for k, v in candidate.items() if k in param_names}

    missing_required = []
    for name, pinfo in sig.parameters.items():
        if name == "self":
            continue
        if pinfo.default is inspect._empty and name not in kwargs:
            missing_required.append(name)
    if missing_required:
        print("\n[DEBUG] create_open_position signature:", sig)
        print("[DEBUG] kwargs we can pass:", kwargs)
        raise SystemExit(f"Missing required args for your trading-ig: {missing_required}")

    print("\n[DEBUG] create_open_position signature:", sig)
    print("[DEBUG] kwargs used:", kwargs)
    return kwargs


def main() -> None:
    # Always load trading-bot/.env regardless of working directory
    env_path = Path(__file__).resolve().parents[2] / ".env"  # trading-bot/.env
    load_dotenv(dotenv_path=env_path, override=False)

    p = argparse.ArgumentParser("IG test trade + discovery (PRACTICE-safe)")
    p.add_argument("--discover", action="store_true", help="Discover markets via search_markets(query)")
    p.add_argument("--query", default="gold", help="Search query for discover mode (e.g. gold, XAUUSD, spot gold)")
    p.add_argument("--limit", type=int, default=25, help="Limit number of markets to inspect in discover mode")

    p.add_argument("--epic", default=os.getenv("IG_TEST_EPIC", "MT.D.GC.FWM4.IP"))

    p.add_argument("--direction", choices=["BUY", "SELL"], default=(os.getenv("IG_TEST_DIRECTION", "BUY").upper()))
    p.add_argument("--size", type=float, default=float(os.getenv("IG_TEST_SIZE", "1")))
    p.add_argument("--stop-distance", type=float, default=float(os.getenv("IG_TEST_STOP_DISTANCE", "5")))
    p.add_argument("--limit-distance", type=float, default=float(os.getenv("IG_TEST_LIMIT_DISTANCE", "10")))
    p.add_argument("--currency", default=os.getenv("IG_TEST_CURRENCY", "USD"))

    p.add_argument("--retries", type=int, default=int(os.getenv("IG_TEST_RETRIES", "5")))
    p.add_argument("--retry-sleep", type=float, default=float(os.getenv("IG_TEST_RETRY_SLEEP", "0.6")))

    p.add_argument(
        "--mode",
        choices=["MARKET", "MARKETABLE_LIMIT"],
        default=os.getenv("IG_TEST_MODE", "MARKETABLE_LIMIT"),
        help="MARKET (may roll) or MARKETABLE_LIMIT (limit near bid/offer)",
    )

    p.add_argument("--env", choices=["PRACTICE", "LIVE"], default=(os.getenv("IG_ENV") or "PRACTICE").upper())
    p.add_argument("--force-live", action="store_true", help="ALLOW LIVE TRADING (DANGEROUS)")
    args = p.parse_args()

    ig = _connect_ig(args.env, args.force_live)

    if args.discover:
        _discover_epics(ig, query=args.query, limit=args.limit)
        return

    info = _market_check(ig, args.epic)
    expiry = info["expiry"]
    bid = info["bid"]
    offer = info["offer"]

    if info["min_deal"] is not None and args.size < info["min_deal"]:
        raise SystemExit(f"size={args.size} < minDealSize={info['min_deal']}")

    tick = 0.1
    if args.mode == "MARKETABLE_LIMIT":
        if args.direction == "BUY":
            level = round(offer + 5 * tick, 1)
        else:
            level = round(bid - 5 * tick, 1)
        order_type = "LIMIT"
        time_in_force = "FILL_OR_KILL"
    else:
        level = None
        order_type = "MARKET"
        time_in_force = None

    print("\n--- ORDER PREVIEW ---")
    print("mode:", args.mode)
    print("direction:", args.direction)
    print("size:", args.size)
    print("expiry used:", expiry)
    print("order_type:", order_type)
    print("level:", level)
    print("stop_distance:", args.stop_distance)
    print("limit_distance:", args.limit_distance)
    print("time_in_force:", time_in_force)

    kwargs = _build_kwargs_for_create_open_position(
        ig=ig,
        currency=args.currency,
        direction=args.direction,
        epic=args.epic,
        expiry=expiry,
        size=args.size,
        stop_distance=args.stop_distance,
        limit_distance=args.limit_distance,
        order_type=order_type,
        level=level,
        time_in_force=time_in_force,
    )

    print("\n--- PLACING TEST ORDER ---")
    last = None
    for attempt in range(1, args.retries + 1):
        try:
            resp = _with_backoff(lambda: ig.create_open_position(**kwargs))
            last = resp
            print(f"\nAttempt {attempt}/{args.retries} response:", resp)

            if not isinstance(resp, dict):
                break

            deal_status = resp.get("dealStatus")
            reason = resp.get("reason")
            deal_ref = resp.get("dealReference") or resp.get("deal_reference")

            if deal_status == "ACCEPTED":
                print("\n✅ Order ACCEPTED")
                if deal_ref:
                    conf = _confirm_by_ref(ig, deal_ref)
                    if conf is not None:
                        print("\n--- DEAL CONFIRMATION ---")
                        print(conf)
                break

            print(f"[WARN] dealStatus={deal_status} reason={reason} dealReference={deal_ref}")

            if reason == "MARKET_ROLLED" and attempt < args.retries:
                time.sleep(args.retry_sleep)
                continue

            if deal_ref:
                conf = _confirm_by_ref(ig, deal_ref)
                if conf is not None:
                    print("\n--- DEAL CONFIRMATION ---")
                    print(conf)
            break

        except Exception as e:
            print(f"[ERROR] Exception on attempt {attempt}/{args.retries}: {e}")
            if attempt < args.retries:
                time.sleep(args.retry_sleep)
                continue
            raise

    print("\nDone.")
    if last is not None:
        print("Last response:", last)


if __name__ == "__main__":
    main()
