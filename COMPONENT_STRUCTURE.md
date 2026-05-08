# Component Structure

## app/main.py
Public API, dashboard, lifecycle

## app/engine.py
Background orchestration and end-to-end trading workflow

## app/kalshi.py
Kalshi API adapter with pagination and signed requests

## app/cache.py
TTL cache abstraction for markets, orderbooks, and balance

## app/classifier.py
Category detection, market normalization, market-type inference

## app/research.py
Category-aware scoring envelope from market quality + operator notes

## app/selector.py
Single-first pool building, candidate construction, ranking

## app/risk.py
Bankroll sizing, duplicate blocking, category exposure checks

## app/services/universe.py
Market persistence and normalized universe writes

## app/services/execution.py
Order creation and execution path

## app/services/audit.py
Daily audit summarization and improvement suggestions

## app/models.py
SQLAlchemy schema
