"""Paper-trading per-market allocation / sub-pool cash unit tests (US + CA)."""

import math
import unittest
from types import SimpleNamespace

from src.core.paper_trading_engine import (
    ALL_MARKETS,
    DEFAULT_ALLOCATIONS,
    allocations_from_excluded,
    compute_market_cash,
    market_allocations_or_default,
    normalize_allocations,
)
from src.models.market import EQUITY_MARKETS


class TestMarketUniverse(unittest.TestCase):
    def test_all_markets_is_enabled_equity_markets(self):
        """ALL_MARKETS mirrors EQUITY_MARKETS (US, CA) and never contains CN/HK"""
        self.assertEqual(ALL_MARKETS, tuple(EQUITY_MARKETS))
        self.assertEqual(ALL_MARKETS, ("US", "CA"))

    def test_default_allocations_us_60_ca_40(self):
        """Defaults are US 60 / CA 40 and sum to 1"""
        self.assertAlmostEqual(DEFAULT_ALLOCATIONS["US"], 0.6)
        self.assertAlmostEqual(DEFAULT_ALLOCATIONS["CA"], 0.4)
        self.assertTrue(math.isclose(sum(DEFAULT_ALLOCATIONS.values()), 1.0, abs_tol=1e-6))


class TestNormalizeAllocations(unittest.TestCase):
    def test_fill_missing_markets(self):
        """Normalize: missing enabled markets are filled with 0 and every enabled market is a key"""
        out = normalize_allocations({"US": 0.6})
        self.assertEqual(set(out.keys()), set(ALL_MARKETS))
        self.assertAlmostEqual(out["US"], 0.6)
        self.assertEqual(out["CA"], 0.0)

    def test_clamp_negative_to_zero(self):
        """Normalize: negative ratios clamp to 0"""
        out = normalize_allocations({"CA": -0.2, "US": 0.5})
        self.assertEqual(out["CA"], 0.0)
        self.assertAlmostEqual(out["US"], 0.5)

    def test_clamp_over_one(self):
        """Normalize: ratios above 1 clamp to 1"""
        out = normalize_allocations({"US": 1.5})
        self.assertEqual(out["US"], 1.0)

    def test_none_input(self):
        """Normalize: None input gives 0 for every enabled market"""
        out = normalize_allocations(None)
        self.assertEqual(out, {"US": 0.0, "CA": 0.0})

    def test_disabled_markets_dropped_and_renormalised(self):
        """Normalize: legacy CN/HK weights are treated as zero and their share is re-spread over enabled markets"""
        out = normalize_allocations({"CN": 0.5, "HK": 0.3, "US": 0.2})
        self.assertEqual(set(out.keys()), {"US", "CA"})
        self.assertAlmostEqual(out["US"], 1.0)
        self.assertEqual(out["CA"], 0.0)
        self.assertNotIn("CN", out)
        self.assertNotIn("HK", out)

    def test_disabled_weight_keeps_operator_ratio_between_enabled(self):
        """Normalize: with CN carrying weight, US/CA keep their relative ratio while absorbing it"""
        out = normalize_allocations({"CN": 0.4, "US": 0.3, "CA": 0.3})
        self.assertAlmostEqual(out["US"], 0.5)
        self.assertAlmostEqual(out["CA"], 0.5)

    def test_enabled_only_config_is_preserved(self):
        """Normalize: a config that only names enabled markets is returned unchanged (no re-scaling)"""
        out = normalize_allocations({"US": 0.5})
        self.assertEqual(out, {"US": 0.5, "CA": 0.0})


class TestAllocationsFromExcluded(unittest.TestCase):
    def test_exclude_ca_renormalizes_rest(self):
        """Migration: excluding CA puts everything in US"""
        out = allocations_from_excluded(["CA"])
        self.assertEqual(out["CA"], 0.0)
        self.assertAlmostEqual(out["US"], 1.0, places=4)

    def test_empty_returns_default(self):
        """Migration: no exclusions falls back to the 60/40 default"""
        out = allocations_from_excluded([])
        for m in ALL_MARKETS:
            self.assertAlmostEqual(out[m], DEFAULT_ALLOCATIONS[m], places=4)

    def test_all_excluded_fallback_default_market(self):
        """Migration: excluding every market falls back to 100% in the default market (US)"""
        out = allocations_from_excluded(["US", "CA"])
        self.assertEqual(out, {"US": 1.0, "CA": 0.0})

    def test_legacy_exclusions_ignored(self):
        """Migration: legacy CN/HK exclusions are ignored and do not disturb the defaults"""
        out = allocations_from_excluded(["CN", "HK"])
        for m in ALL_MARKETS:
            self.assertAlmostEqual(out[m], DEFAULT_ALLOCATIONS[m], places=4)


class TestComputeMarketCash(unittest.TestCase):
    def test_basic(self):
        """Sub-pool cash = initial x ratio + realized - open cost"""
        # 1,000,000 x 50% + 5000 - 300000 = 205000
        self.assertAlmostEqual(
            compute_market_cash(1_000_000, 0.5, 5000, 300000), 205000.0
        )

    def test_zero_ratio(self):
        """Sub-pool cash: a 0 ratio gives 0 initial cash"""
        self.assertEqual(compute_market_cash(1_000_000, 0.0, 0, 0), 0.0)

    def test_over_allocated_negative(self):
        """Sub-pool cash: positions above the budget give a negative value (no room for new entries)"""
        # 1,000,000 x 10% - 200,000 open = -100,000
        self.assertLess(compute_market_cash(1_000_000, 0.1, 0, 200000), 0)


class TestMarketAllocationsOrDefault(unittest.TestCase):
    def test_empty_falls_back_default(self):
        """Account ratios: unconfigured account falls back to the default"""
        acc = SimpleNamespace(market_allocations=None, initial_capital=1_000_000)
        out = market_allocations_or_default(acc)
        self.assertEqual(out, dict(DEFAULT_ALLOCATIONS))

    def test_configured_is_normalized(self):
        """Account ratios: a configured account is normalized and returned"""
        acc = SimpleNamespace(
            market_allocations={"US": 1.0, "CA": 0}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertEqual(out, {"US": 1.0, "CA": 0.0})

    def test_only_disabled_markets_falls_back_default(self):
        """Account ratios: a legacy config naming only CN/HK counts as unconfigured → default 60/40"""
        acc = SimpleNamespace(
            market_allocations={"CN": 0.7, "HK": 0.3}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertEqual(out, dict(DEFAULT_ALLOCATIONS))

    def test_sum_le_one_invariant(self):
        """Account ratios: a sensible config sums to at most 1"""
        acc = SimpleNamespace(
            market_allocations={"US": 0.6, "CA": 0.4}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertTrue(math.isclose(sum(out.values()), 1.0, abs_tol=1e-6))


if __name__ == "__main__":
    unittest.main()
