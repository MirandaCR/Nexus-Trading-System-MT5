"""
Nexus Data Service — Service 1: Data Ingestion.

Aggregates real-time and historical market data from MT5, IBKR, CCXT, and
yfinance, persists to DuckDB, and caches hot ticks in Redis.
"""
