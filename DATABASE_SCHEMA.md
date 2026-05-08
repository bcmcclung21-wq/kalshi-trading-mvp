# Database Schema

## market_snapshots
Latest normalized market record per ticker.

## orderbook_snapshots
Recent orderbook states for shortlisted markets.

## research_notes
Operator or imported research overlays by ticker or category.

## candidate_runs
Every scored candidate that passes selector thresholds.

## order_records
Every dry-run or live order attempt.

## position_snapshots
Periodic position snapshots for reconcile and exposure control.

## audit_runs
Daily performance review and improvement output.

## Scaling note
Schema is intentionally append-friendly so audit and analytics can be expanded later.
