# SeatGeek World Cup Ticket Alert Agent

This repository includes `ticket_price_agent.py`, a Python agent that:

- Finds the SeatGeek event for **World Cup Round of 32 – 1I VS TBD** in **New York/New Jersey**
- Checks the lowest listed ticket price every 5 minutes
- Tracks the historical lowest price locally
- Sends an email alert to `hejia90@hotmail.com` whenever a new historical low is found

## Requirements

- Python 3.9+
- SMTP credentials for sending outbound email

## Configuration

Set environment variables before running:

- `ALERT_SMTP_HOST` (example: `smtp.gmail.com` or your SMTP provider)
- `ALERT_SMTP_PORT` (optional, default: `465`)
- `ALERT_SMTP_USERNAME`
- `ALERT_SMTP_PASSWORD`
- `ALERT_FROM_EMAIL` (optional; defaults to `ALERT_SMTP_USERNAME`)
- `SEATGEEK_EVENT_URL` (optional; if omitted, the script searches SeatGeek automatically)

## Usage

Run once:

```bash
python ticket_price_agent.py --once
```

Run continuously (every 5 minutes):

```bash
python ticket_price_agent.py
```
