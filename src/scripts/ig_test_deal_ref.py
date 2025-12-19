import os
import time
import argparse
from pathlib import Path
from dotenv import load_dotenv
from trading_ig.rest import IGService, ApiExceededException


def _with_backoff(fn, max_retries=6, base_sleep=0.8):
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except ApiExceededException:
            s = base_sleep * (2 ** (attempt - 1))
            print(f"[WARN] rate limit -> sleep {s:.1f}s")
            time.sleep(s)
    raise ApiExceededException()


def _connect_ig(env: str) -> IGService:
    username = os.getenv("IG_SERVICE_USERNAME") or os.getenv("IG_USER")
    password = os.getenv("IG_SERVICE_PASSWORD") or os.getenv("IG_PASS")
    api_key = os.getenv("IG_SERVICE_API_KEY")
    if not username or not password or not api_key:
        raise SystemExit("Missing IG creds in .env")
    acc_type = "DEMO" if env == "PRACTICE" else "LIVE"
    ig = IGService(username=username, password=password, api_key=api_key, acc_type=acc_type)
    _with_backoff(lambda: ig.create_session())
    return ig


def main():
    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    p = argparse.ArgumentParser("IG: fetch deal confirmation by dealReference")
    p.add_argument("--env", choices=["PRACTICE", "LIVE"], default=(os.getenv("IG_ENV") or "PRACTICE").upper())
    p.add_argument("--deal-reference", required=True, help="dealReference returned by create_open_position()")
    args = p.parse_args()

    ig = _connect_ig(args.env)

    if hasattr(ig, "fetch_deal_confirmation"):
        conf = _with_backoff(lambda: ig.fetch_deal_confirmation(args.deal_reference))
    else:
        conf = _with_backoff(lambda: ig.fetch_deal_by_deal_reference(args.deal_reference))

    print("\n--- DEAL CONFIRMATION ---")
    print(conf)


if __name__ == "__main__":
    main()
