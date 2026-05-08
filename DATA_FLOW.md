# Data Flow

1. **Market Sync**
   - Engine requests all open Kalshi markets with pagination
   - Markets are normalized and classified locally
   - Market snapshots are upserted into the database
   - Market list is cached in memory

2. **Trade Cycle**
   - Engine reads cached/global market universe
   - Builds a single-first candidate pool
   - Optional sports-only combos are appended when enabled
   - Fetches orderbooks for shortlisted candidates
   - Merges operator research notes by ticker or category
   - Scores projection, research, confidence, confirmation, and EV bonus
   - Applies risk and exposure filters
   - Ranks singles first, then combos
   - Creates dry-run or live orders
   - Persists candidate and order records

3. **Reconcile**
   - Pulls positions from Kalshi
   - Stores position snapshots for monitoring and risk checks

4. **Daily Audit**
   - Pulls settlements
   - Summarizes wins, losses, PnL, category performance, and issues
   - Persists audit results and improvement steps
