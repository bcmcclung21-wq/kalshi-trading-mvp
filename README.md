# Kalshi Scalable MVP

A clean, scalable, singles-first Kalshi trading application.

## Core product goals
- Support five categories: sports, politics, crypto, climate, economics
- Prioritize high-confidence single-leg trades
- Allow rare sports-only combos up to 4 legs when enabled
- Use fixed bankroll rules:
  - 1 leg: 2.00%
  - 2 legs: 1.00%
  - 3 legs: 0.75%
  - 4 legs: 0.50%
- Use research, confidence, and market quality first; EV is a bonus, not the sole selector
- Run a daily audit to learn from prior trades

## Required environment variables
- `KALSHI_API_KEY_ID`
- `KALSHI_PRIVATE_KEY_PEM`
- `DASHBOARD_BASE_URL`

No other environment variables are required for the minimal production version.

## Local run
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Test
```bash
PYTHONPATH=. pytest
```
