# Poly Trading MVP

A clean, scalable, singles-first Polymarket US trading application.

## Core product goals
- Support five categories: sports, politics, crypto, climate, economics
- Prioritize high-confidence single-leg trades
- Keep execution aligned with the Polymarket US Python SDK
- Use fixed bankroll rules:
  - 1 leg: 2.00%
  - 2 legs: 1.00%
  - 3 legs: 0.75%
  - 4 legs: 0.50%
- Use research, confidence, and market quality first; EV is a bonus, not the sole selector
- Run a daily audit to learn from prior trades

## Required Railway environment variables
- `POLYMARKET_KEY_ID`
- `POLYMARKET_SECRET_KEY`
- `DASHBOARD_BASE_URL`

## Recommended runtime variables
- `APP_NAME=Poly Trading MVP`
- `AUTO_EXECUTE=true` after credential validation to allow live execution
- `ALLOW_COMBOS=false`

No wallet private key, passphrase, signature type, or funder address are required for the Polymarket US SDK path.

## Local run
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Test
```bash
PYTHONPATH=. pytest
```
# deploy trigger 2026-05-13T20:36:40Z
