#!/usr/bin/env python3
import argparse
import json
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


EMAIL_SUBJECT = "world cup ticket alert"
POLL_SECONDS = 300
MIN_MATCH_SCORE = 4
STATE_FILE = Path(__file__).resolve().parent / ".ticket_agent_state.json"
TARGET_QUERY = "world cup game round of 32 1I vs TBD new york new jersey"


def fetch_html(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            )
        },
    )
    with urlopen(req, timeout=30) as response:  # nosec B310
        return response.read().decode("utf-8", errors="replace")


def search_event_url() -> Optional[str]:
    query = quote(TARGET_QUERY)
    search_url = f"https://seatgeek.com/search?search={query}"
    html = fetch_html(search_url)

    urls = []
    seen = set()
    for match in re.finditer(r"https:\\/\\/seatgeek\.com\\/[a-zA-Z0-9\-\/_]+tickets", html):
        url = match.group(0).replace("\\/", "/")
        if url not in seen:
            seen.add(url)
            urls.append(url)

    best_url = None
    best_score = -1
    for url in urls:
        lowered_url = url.lower()
        score = 0
        for keyword in ("world-cup", "round-of-32", "1i", "tbd"):
            if keyword in lowered_url:
                score += 1
        if "new-york" in lowered_url or "new-jersey" in lowered_url:
            score += 1

        if "world-cup" in lowered_url and "round-of-32" in lowered_url and score > best_score:
            best_score = score
            best_url = url

    # Require core event terms plus at least two additional signals to reduce false matches.
    if best_score >= MIN_MATCH_SCORE:
        return best_url
    return None


def extract_lowest_price(html: str) -> Optional[float]:
    patterns = [
        r'"lowest_price"\s*:\s*(\d+(?:\.\d+)?)',
        r'"lowPrice"\s*:\s*(\d+(?:\.\d+)?)',
        r'"min_price"\s*:\s*(\d+(?:\.\d+)?)',
    ]
    prices = []
    for pattern in patterns:
        for match in re.finditer(pattern, html):
            prices.append(float(match.group(1)))

    for script_match in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        payload = script_match.group(1).strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for item in data if isinstance(data, list) else [data]:
            offers = item.get("offers") if isinstance(item, dict) else None
            candidates = offers if isinstance(offers, list) else [offers] if offers else []
            for offer in candidates:
                if isinstance(offer, dict):
                    for key in ("lowPrice", "price"):
                        value = offer.get(key)
                        if isinstance(value, (int, float)):
                            prices.append(float(value))

    return min(prices) if prices else None


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def send_email_alert(
    lowest_price: float,
    event_url: str,
    recipient: str,
    checked_at: str,
    previous_historical_low: Optional[float],
    force_send: bool = False,
) -> None:
    smtp_host = os.getenv("ALERT_SMTP_HOST")
    smtp_port = int(os.getenv("ALERT_SMTP_PORT", "465"))
    smtp_user = os.getenv("ALERT_SMTP_USERNAME")
    smtp_password = os.getenv("ALERT_SMTP_PASSWORD")
    from_email = os.getenv("ALERT_FROM_EMAIL", smtp_user or "")

    if not all([smtp_host, smtp_user, smtp_password, from_email]):
        raise RuntimeError(
            "Missing SMTP settings. Set ALERT_SMTP_HOST, ALERT_SMTP_USERNAME, "
            "ALERT_SMTP_PASSWORD, and ALERT_FROM_EMAIL (or ALERT_SMTP_USERNAME)."
        )

    msg = EmailMessage()
    msg["Subject"] = EMAIL_SUBJECT
    msg["From"] = from_email
    msg["To"] = recipient

    if force_send:
        historical_text = (
            f"${previous_historical_low:.2f}" if previous_historical_low is not None else "none recorded yet"
        )
        msg.set_content(
            "Current ticket price check (manually requested).\n"
            f"Checked at (UTC): {checked_at}\n"
            f"Current lowest price: ${lowest_price:.2f}\n"
            f"Historical low on record: {historical_text}\n"
            f"Event: {event_url}"
        )
    else:
        previous_text = (
            f"${previous_historical_low:.2f}" if previous_historical_low is not None else "none (first result)"
        )
        msg.set_content(
            "A new historical low ticket price was detected.\n"
            f"Checked at (UTC): {checked_at}\n"
            f"Previous historical low: {previous_text}\n"
            f"Lowest price: ${lowest_price:.2f}\n"
            f"Event: {event_url}"
        )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(msg)


def run_check(force_send: bool = False) -> None:
    event_url = os.getenv("SEATGEEK_EVENT_URL")
    if not event_url:
        event_url = search_event_url()
    if not event_url:
        raise RuntimeError("Could not locate the target SeatGeek event URL.")

    html = fetch_html(event_url)
    current_price = extract_lowest_price(html)
    if current_price is None:
        raise RuntimeError("Could not extract a lowest ticket price from the event page.")

    state = load_state()
    historical_low = state.get("historical_low")
    previous_historical_low = float(historical_low) if historical_low is not None else None
    should_alert = force_send or previous_historical_low is None or current_price < previous_historical_low

    checked_at = datetime.now(timezone.utc).isoformat()
    recipient = os.getenv("ALERT_TO_EMAIL")
    if not recipient:
        raise RuntimeError("Missing ALERT_TO_EMAIL environment variable.")
    state["last_checked_at"] = checked_at
    state["last_price"] = current_price
    state["event_url"] = event_url

    if should_alert:
        send_email_alert(
            current_price,
            event_url,
            recipient,
            checked_at,
            previous_historical_low,
            force_send=force_send,
        )
        if not force_send:
            state["historical_low"] = current_price
            state["last_alert_at"] = checked_at
        print(f"[{checked_at}] Current price: ${current_price:.2f} (email sent to {recipient})")
    else:
        print(
            f"[{checked_at}] Current price: ${current_price:.2f} | "
            f"Historical low: ${previous_historical_low:.2f}"
        )
    save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Track SeatGeek ticket prices and alert on new historical lows."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one price check immediately and exit.",
    )
    parser.add_argument(
        "--send-now",
        action="store_true",
        help="Fetch the current price, email it immediately, and exit (regardless of historical low).",
    )
    args = parser.parse_args()

    if args.send_now:
        run_check(force_send=True)
        return 0

    if args.once:
        run_check()
        return 0

    while True:
        try:
            run_check()
        except (RuntimeError, URLError, HTTPError, TimeoutError) as exc:
            checked_at = datetime.now(timezone.utc).isoformat()
            print(f"[{checked_at}] Check failed: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
