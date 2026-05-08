# Architecture

## System style
A modular monolith optimized for Railway deployment and future service extraction.

## Runtime components
1. **FastAPI API + dashboard**
2. **Trading engine**
   - market sync loop
   - trade cycle loop
   - reconcile loop
   - daily audit loop
3. **Kalshi client**
   - authenticated API access
   - pagination
   - orderbook retrieval
   - balance, orders, positions, settlements
4. **Persistence**
   - SQLite for the minimal production version
   - schema written to be PostgreSQL-friendly
5. **In-memory TTL cache**
   - global open markets
   - orderbooks
   - balance
6. **Selection pipeline**
   - normalization
   - local category classification
   - single-first filtering
   - research envelope scoring
   - bankroll/risk gating
   - execution

## Scaling path
- Split Kalshi access into its own service
- Move background loops to worker processes
- Replace SQLite with PostgreSQL
- Replace in-memory cache with Redis
- Add external research adapters per category
