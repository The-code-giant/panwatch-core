#!/usr/bin/env python
"""List (default) or delete (``--yes``) database rows that belong to markets which are
no longer enabled (see ``ENABLED_MARKETS`` in ``src/models/market.py``).

Covers: watchlist stocks, real-account positions, price alert rules (+ their hits) and
paper-trading positions. Nothing is deleted unless ``--yes`` is passed; the default run
only prints what would be removed, with counts.

Usage:
    python scripts/prune_disabled_markets.py            # list only
    python scripts/prune_disabled_markets.py --yes      # delete
    ENABLED_MARKETS=US,CA python scripts/prune_disabled_markets.py
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="actually delete the rows (default: list only)")
    args = parser.parse_args()

    from src.models.market import ENABLED_MARKETS, is_enabled
    from src.web.database import SessionLocal, init_db
    from src.web.models import (
        PaperTradingPosition,
        Position,
        PriceAlertHit,
        PriceAlertRule,
        Stock,
    )

    try:
        init_db()
    except Exception:
        pass

    db = SessionLocal()
    try:
        stocks = [s for s in db.query(Stock).all() if not is_enabled(s.market)]
        stock_ids = {s.id for s in stocks}
        positions = (
            db.query(Position).filter(Position.stock_id.in_(stock_ids)).all() if stock_ids else []
        )
        alert_rules = (
            db.query(PriceAlertRule).filter(PriceAlertRule.stock_id.in_(stock_ids)).all()
            if stock_ids else []
        )
        rule_ids = {r.id for r in alert_rules}
        alert_hits = (
            db.query(PriceAlertHit).filter(PriceAlertHit.rule_id.in_(rule_ids)).all() if rule_ids else []
        )
        paper_positions = [
            p for p in db.query(PaperTradingPosition).all() if not is_enabled(p.stock_market)
        ]

        print(f"Enabled markets: {', '.join(ENABLED_MARKETS)}")
        print("Rows in disabled markets:")
        print(f"  watchlist stocks       : {len(stocks)}")
        for s in stocks:
            print(f"    - #{s.id} {s.market}:{s.symbol} {s.name}")
        print(f"  account positions      : {len(positions)}")
        for p in positions:
            print(f"    - #{p.id} stock_id={p.stock_id} qty={p.quantity} cost={p.cost_price}")
        print(f"  price alert rules      : {len(alert_rules)} (+ {len(alert_hits)} hits)")
        for r in alert_rules:
            print(f"    - #{r.id} {r.name or ''} stock_id={r.stock_id}")
        print(f"  paper trading positions: {len(paper_positions)}")
        for p in paper_positions:
            print(f"    - #{p.id} {p.stock_market}:{p.stock_symbol} status={p.status} qty={p.quantity}")

        total = len(stocks) + len(positions) + len(alert_rules) + len(alert_hits) + len(paper_positions)
        if total == 0:
            print("Nothing to prune.")
            return 0
        if not args.yes:
            print(f"\n{total} row(s) would be deleted. Re-run with --yes to delete them.")
            return 0

        for h in alert_hits:
            db.delete(h)
        for r in alert_rules:
            db.delete(r)
        for p in paper_positions:
            db.delete(p)
        for p in positions:
            db.delete(p)
        for s in stocks:
            db.delete(s)  # cascades StockAgent rows
        db.commit()
        print(f"\nDeleted {total} row(s).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
