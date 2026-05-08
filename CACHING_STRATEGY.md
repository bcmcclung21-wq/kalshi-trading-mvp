# Caching Strategy

## Minimal production cache
In-memory TTL cache.

## Cached objects
- Open markets: 20 seconds
- Orderbooks: 6 seconds
- Balance: 10 seconds
- Summary/UI reads: derived from DB and engine state

## Why this works for MVP
- Reduces duplicate Kalshi requests
- Keeps the trading loop responsive
- Avoids introducing Redis before necessary

## Scale-up path
- Replace TTL cache with Redis
- Add market diff cache and event-based invalidation
- Add orderbook fan-out cache if workers are split
