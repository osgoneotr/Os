"""Tests for the parts that would silently cost money if they broke.

Run with:  python3 tests/test_pipeline.py
(No pytest needed — plain unittest, so it works on a bare Python install.)
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autopilot import config as cfg_mod
from autopilot import fees, listings, pricing, reports, sourcing
from autopilot.models import Candidate, Comps
from autopilot.sources import tabular

CFG = cfg_mod.load()


class TestParseMoney(unittest.TestCase):
    """The bug that started it all: a product title read as a price."""

    def test_currency_forms(self):
        self.assertEqual(tabular.parse_money("£12.50"), 12.50)
        self.assertEqual(tabular.parse_money("12.50"), 12.50)
        self.assertEqual(tabular.parse_money(" 12 "), 12.0)
        self.assertEqual(tabular.parse_money("GBP 45.99"), 45.99)
        self.assertEqual(tabular.parse_money("£1,234.56"), 1234.56)
        self.assertEqual(tabular.parse_money("12,50"), 12.50)

    def test_titles_are_not_prices(self):
        # "North Face Nuptse 700" must not parse as £700.
        self.assertIsNone(tabular.parse_money("North Face Nuptse 700 black L"))
        self.assertIsNone(tabular.parse_money("Sony WM-FX290"))
        self.assertIsNone(tabular.parse_money("Free postage"))
        self.assertIsNone(tabular.parse_money(""))
        self.assertIsNone(tabular.parse_money(None))

    def test_zero_and_negative_rejected(self):
        self.assertIsNone(tabular.parse_money("0"))
        self.assertIsNone(tabular.parse_money("£0.00"))


class TestSoldHtml(unittest.TestCase):
    HTML = """
    <li><span class="s-item__price">£38.00</span><span>+£3.95 postage</span></li>
    <li><span class="s-item__price">£45.00</span><span>Free postage</span></li>
    <li><span class="s-item__price">£30.00 to £40.00</span></li>
    <script>var t = "£999.99";</script>
    """

    def test_excludes_postage_and_scripts(self):
        prices, _ = tabular.parse_sold_html(self.HTML)
        self.assertIn(38.00, prices)
        self.assertIn(45.00, prices)
        self.assertIn(35.00, prices)      # midpoint of the range
        self.assertNotIn(3.95, prices)    # postage line
        self.assertNotIn(999.99, prices)  # script template

    def test_empty_html_reports_why(self):
        prices, notes = tabular.parse_sold_html("<html><body>nothing here</body></html>")
        self.assertEqual(prices, [])
        self.assertTrue(notes)


class TestComps(unittest.TestCase):
    def test_outliers_dropped(self):
        prices = [50, 55, 60, 58, 62, 57, 59, 61, 5000]
        comps = pricing.build_comps("test", prices, source="ebay_sold_csv", cfg=CFG)
        self.assertLess(comps.high, 100)
        self.assertTrue(any("outlier" in w for w in comps.warnings))

    def test_active_listings_are_calibrated_down(self):
        prices = [100.0] * 10
        active = pricing.build_comps("t", prices, source="ebay_browse_active", category="clothing", cfg=CFG)
        sold = pricing.build_comps("t", prices, source="ebay_sold_csv", category="clothing", cfg=CFG)
        self.assertLess(active.median, sold.median)
        # Asking-price data never earns high confidence, however many samples.
        self.assertNotEqual(active.confidence, "high")

    def test_confidence_scales_with_sample_size(self):
        few = pricing.build_comps("t", [50, 52], source="ebay_sold_csv", cfg=CFG)
        many = pricing.build_comps("t", [50, 51, 52, 53, 54, 55, 56, 57, 58, 59], source="ebay_sold_csv", cfg=CFG)
        self.assertEqual(few.confidence, "low")
        self.assertEqual(many.confidence, "high")

    def test_no_prices_is_unusable(self):
        comps = pricing.build_comps("t", [], source="none", cfg=CFG)
        self.assertFalse(comps.is_usable)
        self.assertEqual(comps.confidence, "none")


class TestCompMatching(unittest.TestCase):
    def test_matches_same_product(self):
        self.assertTrue(sourcing._is_comparable("The North Face Nuptse 700", "TNF Nuptse 700 jacket black L"))

    def test_rejects_different_product(self):
        self.assertFalse(sourcing._is_comparable("The North Face Nuptse 700", "North Face beanie hat"))
        self.assertFalse(sourcing._is_comparable("Sony WM-FX290", "Sony Bravia television"))


class TestFees(unittest.TestCase):
    def test_vinted_seller_pays_no_postage(self):
        cost, note = fees.postage_cost("vinted", "small_parcel_1kg", CFG)
        self.assertEqual(cost, 0.0)
        self.assertIn("buyer", note)

    def test_ebay_absorbs_postage_by_default(self):
        cost, _ = fees.postage_cost("ebay_uk", "small_parcel_1kg", CFG)
        self.assertGreater(cost, 0.0)

    def test_facebook_local_has_no_postage_or_packaging(self):
        cost, _ = fees.postage_cost("facebook", "small_parcel_1kg", CFG)
        self.assertEqual(cost, 0.0)
        item = Candidate(title="t", ask_price=5, category="clothing")
        pack, _ = fees.packaging_cost(item, "facebook", CFG)
        self.assertEqual(pack, 0.0)

    def test_business_seller_pays_more_than_private(self):
        private, _ = fees.platform_fee("ebay_uk", 50.0, CFG)
        import copy

        business_cfg = cfg_mod.Config(copy.deepcopy(CFG.data), CFG.source)
        business_cfg.data["channels"]["ebay_uk"]["seller_type"] = "business"
        business, _ = fees.platform_fee("ebay_uk", 50.0, business_cfg)
        self.assertGreater(business, private)

    def test_heavier_items_get_bigger_postage_bands(self):
        light = fees.postage_band_for(Candidate(title="t", ask_price=1, weight_kg=0.5), CFG)
        heavy = fees.postage_band_for(Candidate(title="t", ask_price=1, weight_kg=6.0), CFG)
        light_max = CFG.get(f"postage.bands.{light}.max_kg")
        heavy_max = CFG.get(f"postage.bands.{heavy}.max_kg")
        self.assertLess(light_max, heavy_max)


def _good_comps(median: float = 60.0) -> Comps:
    prices = [median - 8, median - 4, median, median, median + 4, median + 8, median + 2, median - 2]
    return pricing.build_comps("test item", prices, source="ebay_sold_csv", cfg=CFG)


class TestVerdicts(unittest.TestCase):
    def test_clear_winner_is_a_buy(self):
        item = Candidate(title="The North Face Nuptse", ask_price=14.0, brand="The North Face",
                         model="Nuptse", category="clothing", condition="good")
        analysis = pricing.analyse(item, _good_comps(), channel="vinted", cfg=CFG)
        self.assertEqual(analysis.verdict.action, "BUY")
        self.assertGreater(analysis.economics.net_profit, 8.0)

    def test_thin_margin_is_a_pass(self):
        item = Candidate(title="Something", ask_price=24.0, brand="Nike", category="clothing", condition="good")
        analysis = pricing.analyse(item, _good_comps(30.0), channel="vinted", cfg=CFG)
        self.assertEqual(analysis.verdict.action, "PASS")

    def test_no_comps_is_review_not_pass(self):
        """Missing evidence is an unknown, not a judgement about the item."""
        item = Candidate(title="Mystery thing", ask_price=5.0, category="clothing")
        empty = pricing.build_comps("q", [], source="none", cfg=CFG)
        analysis = pricing.analyse(item, empty, channel="vinted", cfg=CFG)
        self.assertEqual(analysis.verdict.action, "REVIEW")

    def test_thin_comps_downgrade_buy_to_review(self):
        item = Candidate(title="Nike thing", ask_price=5.0, brand="Nike", category="clothing", condition="good")
        thin = pricing.build_comps("q", [60.0, 62.0], source="ebay_sold_csv", cfg=CFG)
        analysis = pricing.analyse(item, thin, channel="vinted", cfg=CFG)
        self.assertEqual(analysis.verdict.action, "REVIEW")

    def test_over_budget_item_is_a_pass(self):
        item = Candidate(title="Expensive", ask_price=500.0, brand="Nike", category="clothing", condition="good")
        analysis = pricing.analyse(item, _good_comps(2000.0), channel="vinted", cfg=CFG)
        self.assertEqual(analysis.verdict.action, "PASS")
        self.assertTrue(any("cap" in r for r in analysis.verdict.reasons))

    def test_fast_fashion_is_a_pass_whatever_the_comps(self):
        item = Candidate(title="Primark hoodie", ask_price=1.0, brand="Primark", category="clothing", condition="good")
        analysis = pricing.analyse(item, _good_comps(), channel="vinted", cfg=CFG)
        self.assertEqual(analysis.verdict.action, "PASS")

    def test_counterfeit_keywords_are_blocked(self):
        item = Candidate(title="Replica Stone Island jumper 1:1", ask_price=12.0, category="clothing")
        blocked = listings.check_blocked(item, CFG)
        self.assertIsNotNone(blocked)
        analysis = pricing.analyse(item, _good_comps(), channel="vinted", cfg=CFG, blocked_reason=blocked)
        self.assertEqual(analysis.verdict.action, "BLOCKED")


class TestChannelChoice(unittest.TestCase):
    def test_preference_order_survives_a_small_difference(self):
        """Facebook has no fees, so it wins on raw profit — it shouldn't win by default."""
        item = Candidate(title="Nike jacket", ask_price=10.0, brand="Nike", category="clothing", condition="good")
        analysis = pricing.best_channel(item, _good_comps(), cfg=CFG)
        self.assertEqual(analysis.economics.channel, "vinted")

    def test_facebook_priced_below_ebay_comps(self):
        item = Candidate(title="Nike jacket", ask_price=10.0, brand="Nike", category="clothing", condition="good")
        comps = _good_comps()
        fb = pricing.analyse(item, comps, channel="facebook", cfg=CFG)
        vinted = pricing.analyse(item, comps, channel="vinted", cfg=CFG)
        self.assertLess(fb.economics.list_price, vinted.economics.list_price)


class TestQualityGate(unittest.TestCase):
    def _package(self, item: Candidate):
        return listings.build_package(pricing.analyse(item, _good_comps(), channel="vinted", cfg=CFG), cfg=CFG)

    def test_untested_electronics_never_claim_to_work(self):
        item = Candidate(title="Sony amp fully working", ask_price=10.0, brand="Sony",
                         category="audio", condition="good", tested_confirmed=False)
        package = self._package(item)
        blob = f"{package.title_seo} {package.title_style} {package.description}".lower()
        self.assertNotIn("fully working", blob)
        self.assertIn("untested", blob)

    def test_tested_electronics_may_say_so(self):
        item = Candidate(title="Sony amp", ask_price=10.0, brand="Sony", category="audio",
                         condition="good", tested_confirmed=True)
        package = self._package(item)
        self.assertIn("Tested: yes", package.description)

    def test_authenticity_claims_never_reach_the_listing(self):
        item = Candidate(title="Nike trainers 100% authentic", ask_price=10.0, brand="Nike",
                         category="trainers", condition="good")
        package = self._package(item)
        blob = f"{package.title_seo} {package.title_style} {package.description}".lower()
        self.assertNotIn("100% authentic", blob)
        self.assertNotIn("guaranteed genuine", blob)

    def test_audit_strips_authenticity_claims_from_supplied_text(self):
        item = Candidate(title="Nike trainers", ask_price=10.0, brand="Nike", condition="good")
        cleaned, stripped, warnings = listings.audit_claims(
            "Nike trainers, 100% authentic, guaranteed genuine.", item, CFG
        )
        self.assertNotIn("100% authentic", cleaned.lower())
        self.assertNotIn("guaranteed genuine", cleaned.lower())
        self.assertTrue(stripped)
        self.assertTrue(warnings)

    def test_brand_new_softened_for_used_items(self):
        item = Candidate(title="Brand new jacket", ask_price=10.0, brand="Nike",
                         category="clothing", condition="good")
        package = self._package(item)
        self.assertNotIn("brand new", package.description.lower())

    def test_titles_respect_channel_limits(self):
        long_title = "Nike " + "extremely descriptive words " * 12
        item = Candidate(title=long_title, ask_price=10.0, brand="Nike", model="Air Max 90 Premium Edition",
                         colour="Triple Black White Grey", size="9", category="trainers", condition="good")
        for channel, limit in (("ebay_uk", 80), ("vinted", 100)):
            package = listings.build_package(
                pricing.analyse(item, _good_comps(), channel=channel, cfg=CFG), channel=channel, cfg=CFG
            )
            self.assertLessEqual(len(package.title_seo), limit, channel)
            self.assertLessEqual(len(package.title_style), limit, channel)

    def test_unevidenced_attributes_are_flagged(self):
        item = Candidate(title="jacket", ask_price=10.0, brand="Stone Island", size="L",
                         category="clothing", condition="good")
        package = self._package(item)
        self.assertTrue(any("Unconfirmed" in w for w in package.warnings))

    def test_confirmed_attributes_are_not_flagged(self):
        item = Candidate(
            title="jacket", ask_price=10.0, brand="Stone Island", size="L",
            category="clothing", condition="good", photos=["a.jpg"],
            evidence={"brand": "photo", "model": "photo", "size": "photo", "material": "photo"},
        )
        package = self._package(item)
        self.assertFalse(any("Unconfirmed" in w for w in package.warnings), package.warnings)


class TestReports(unittest.TestCase):
    ROWS = [
        {"sku": "A", "status": "sold", "category": "clothing", "buy_price": "10",
         "sold_price": "50", "platform_fee": "0", "postage_cost": "0", "packaging_cost": "0.35",
         "est_net_profit": "38", "listed_at": "2026-06-01", "sold_at": "2026-06-11"},
        {"sku": "B", "status": "listed", "category": "trainers", "buy_price": "12",
         "list_price": "40", "min_price": "30", "listed_at": "2026-01-01"},
        {"sku": "C", "status": "candidate", "category": "audio", "buy_price": "8"},
    ]

    def test_realised_profit_reconstructed_from_parts(self):
        self.assertAlmostEqual(reports.realised_profit(self.ROWS[0]), 39.65, places=2)

    def test_summary_numbers(self):
        summary = reports.summarise(self.ROWS, CFG)
        self.assertEqual(summary["counts"]["sold"], 1)
        self.assertEqual(summary["money"]["revenue"], 50.0)
        self.assertEqual(summary["money"]["capital_committed"], 12.0)
        self.assertEqual(summary["velocity"]["sell_through_pct"], 50.0)

    def test_stale_inventory_detected_with_an_action(self):
        stale = reports.stale_inventory(self.ROWS, CFG)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["sku"], "B")
        self.assertTrue(stale[0]["suggested_action"])

    def test_report_renders_without_crashing(self):
        self.assertIn("RESELL AUTOPILOT", reports.render_text(self.ROWS, CFG))

    def test_empty_ledger_renders(self):
        self.assertIn("RESELL AUTOPILOT", reports.render_text([], CFG))


class TestLedgerRoundTrip(unittest.TestCase):
    """A ledger row must be able to become a priced analysis again.

    The ledger stores a median, not the full comp sample. An earlier version
    rebuilt the comps without the derived fields the pricing engine checks, so
    re-listing an item to a different channel silently produced a £0.00 price.
    """

    ROW = {
        "sku": "TEST-1", "status": "bought", "category": "clothing",
        "brand": "The North Face", "model": "Nuptse 700", "size": "L",
        "condition": "good", "buy_price": "14.00", "list_price": "54.00",
        "min_price": "38.88", "comps_n": "9", "comps_median": "60.00",
        "comps_source": "ebay_sold_csv", "confidence": "high", "channel": "vinted",
        "est_net_profit": "40.65", "verdict": "BUY",
    }

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import make_listings

        self.make_listings = make_listings

    def test_row_rebuilds_into_a_priced_analysis(self):
        analysis = self.make_listings.row_to_analysis(dict(self.ROW), CFG)
        self.assertEqual(analysis.candidate.brand, "The North Face")
        self.assertEqual(analysis.economics.list_price, 54.00)
        self.assertEqual(analysis.comps.median, 60.00)

    def test_rebuilt_comps_can_price_another_channel(self):
        analysis = self.make_listings.row_to_analysis(dict(self.ROW), CFG)
        rebuilt = pricing.build_comps(
            analysis.comps.query, [analysis.comps.median],
            source=analysis.comps.source, category="clothing", cfg=CFG,
        )
        self.assertTrue(rebuilt.is_usable)
        facebook = pricing.analyse(analysis.candidate, rebuilt, channel="facebook", cfg=CFG)
        self.assertGreater(facebook.economics.list_price, 0.0)
        self.assertLess(facebook.economics.list_price, 54.00)


class TestAttributeInference(unittest.TestCase):
    def test_brand_and_size_from_title_are_marked_unverified(self):
        item = sourcing.infer_attributes(
            Candidate(title="Nike Air Max 90 trainers UK 9", ask_price=10.0), CFG
        )
        self.assertEqual(item.brand, "Nike")
        self.assertEqual(item.size, "9")
        self.assertEqual(item.category, "trainers")
        # Guessed, so it must not count as evidence.
        self.assertNotIn(item.evidence["brand"], listings.TRUSTED_EVIDENCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
