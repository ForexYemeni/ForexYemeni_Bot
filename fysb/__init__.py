"""FOREXYEMENI — Telegram trading-signals platform.

Modular platform:
  config      — environment & brand constants
  theme       — design system (templates, colors, formatting, HTML sanitizer)
  db          — SQLite layer: WAL, safe additive migrations, indexes, transactions
  instruments — per-symbol pip/point/contract specifications
  services    — users, plans, subscriptions, payments, support, broadcast, backup
  engines     — market data (real-spot truth layer), indicators, strategies
                (ICT/SMC/PA), signal pipeline, lifecycle tracker, risk engine,
                demo simulator

Broker adapters exist and require explicit policy/credentials. No release or
software test is approval to enable LIVE execution.
"""
__version__ = "8.14.22"
