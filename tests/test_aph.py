"""Kiểm thử tools/aph.py. Chạy: python3 -m unittest discover -s tests -v

Dữ liệu trong test chỉ để kiểm tra logic công cụ, ghi vào thư mục tạm, không bao giờ ghi vào kho thật.
"""
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import aph  # noqa: E402

BUSINESS = {"test_capital_vnd": 10_000_000}


def full_inputs(**over):
    base = {
        "sell_price": {"conservative": 250000, "base": 290000, "optimistic": 320000, "ref": "user:test"},
        "unit_cost": {"conservative": 150000, "base": 140000, "optimistic": 130000, "ref": "user:test"},
        "inbound_shipping": {"conservative": 10000, "base": 8000, "optimistic": 6000, "ref": "user:test"},
        "platform_fee_pct": {"conservative": 15, "base": 13, "optimistic": 12, "ref": "user:test"},
        "payment_fee_pct": {"conservative": 5, "base": 5, "optimistic": 5, "ref": "user:test"},
        "ads_pct": {"conservative": 15, "base": 10, "optimistic": 7, "ref": "user:test"},
        "seller_shipping": {"conservative": 0, "base": 0, "optimistic": 0, "ref": "user:test"},
        "packaging": {"conservative": 5000, "base": 4000, "optimistic": 3000, "ref": "user:test"},
        "ops_per_unit": {"conservative": 5000, "base": 4000, "optimistic": 3000, "ref": "user:test"},
        "return_rate_pct": {"conservative": 8, "base": 5, "optimistic": 3, "ref": "user:test"},
        "return_loss_pct": {"conservative": 50, "base": 40, "optimistic": 30, "ref": "user:test"},
        "other_per_unit": {"conservative": 0, "base": 0, "optimistic": 0, "ref": "user:test"},
        "fixed_test_cost": {"conservative": 1500000, "base": 1500000, "optimistic": 1500000, "ref": "user:test"},
        "moq": {"conservative": 30, "base": 30, "optimistic": 30, "ref": "user:test"},
    }
    base.update(over)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = aph.Store(self.tmp.name)
        self.st.init()
        self.run = self.st.new_run("manual", "test", "quick")["run_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def product(self, name="Cảm biến rò rỉ nước Zigbee", fk="water-leak-sensor|zigbee", force_new=False, **kw):
        d = {"canonical_name": name, "category": "smart_home", "function_key": fk, **kw}
        return self.st.add_product(self.run, d, force_new)

    def obs(self, pid, metric="price", url="https://a.example/x", verification="observed", conf=0.9, **kw):
        d = {"product_id": pid, "agent": "competitor_analyst", "metric": metric, "value": 1,
             "source_type": "marketplace", "verification": verification, "confidence": conf, "source_url": url, **kw}
        if verification == "observed" and "evidence_excerpt" not in kw:
            d["evidence_excerpt"] = f"giá trị đọc được trên trang: {d['value']}"
        return self.st.append(self.run, "observation", d)


class TestDedup(Base):
    def test_same_function_key_returns_existing(self):
        a = self.product()
        b = self.product(name="Water leak sensor Zigbee", fk="Water Leak Sensor | Zigbee")
        self.assertEqual(a["status"], "created")
        self.assertEqual(b["status"], "existing")
        self.assertEqual(a["product_id"], b["product_id"])
        self.assertIn("Water leak sensor Zigbee", self.st.product(a["product_id"])["aliases"])

    def test_alias_in_other_language_matches(self):
        a = self.product(aliases=["漏水传感器 zigbee"])
        b = self.product(name="漏水传感器 zigbee", fk="leak-detector|zb")
        self.assertEqual(b["status"], "existing")
        self.assertEqual(a["product_id"], b["product_id"])

    def test_variant_is_not_auto_merged(self):
        self.product()
        b = self.product(name="Cảm biến rò rỉ nước WiFi", fk="water-leak-sensor|wifi")
        self.assertEqual(b["status"], "needs_review")
        c = self.st.add_product(self.run, {"canonical_name": "Cảm biến rò rỉ nước WiFi", "category": "smart_home",
                                           "function_key": "water-leak-sensor|wifi"}, force_new=True)
        self.assertEqual(c["status"], "created")
        self.assertEqual(len(self.st.products()), 2)
        self.assertEqual(len(aph.duplicates(self.st)), 1)  # flagged for review, never merged

    def test_unrelated_names_create_new(self):
        self.product()
        b = self.product(name="Ổ cắm thông minh đo điện năng", fk="smart-plug|energy-meter")
        self.assertEqual(b["status"], "created")

    def test_diacritics_normalised(self):
        self.assertEqual(aph.norm_text("Cảm biến ĐIỆN"), "cam bien dien")


class TestSourceRules(Base):
    def test_observed_requires_source_url(self):
        pid = self.product()["product_id"]
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "observation", {
                "product_id": pid, "agent": "competitor_analyst", "metric": "price", "value": 1,
                "source_type": "marketplace", "verification": "observed", "confidence": 0.9})

    def test_estimated_requires_range_and_note(self):
        pid = self.product()["product_id"]
        base = {"product_id": pid, "agent": "profit_analyst", "metric": "unit_cost", "value": 100,
                "source_type": "user", "verification": "estimated", "confidence": 0.3}
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "observation", {**base, "note": "x"})
        r = self.st.append(self.run, "observation", {**base, "note": "x", "range_low": 80, "range_high": 120})
        self.assertTrue(r["id"].startswith("obs_"))

    def test_bad_enum_and_extra_field_rejected(self):
        pid = self.product()["product_id"]
        with self.assertRaises(aph.APHError):
            self.obs(pid, verification="guess")
        with self.assertRaises(aph.APHError):
            self.obs(pid, made_up_field=1)

    def test_unknown_product_rejected(self):
        with self.assertRaises(aph.APHError):
            self.obs("prd_missing")

    def test_unknown_run_rejected(self):
        with self.assertRaises(aph.APHError):
            self.st.append("run_20260101T000000_zzzz", "access", {"url": "https://x", "status": "ok"})


class TestEvidenceExcerpt(Base):
    def base(self, pid, **kw):
        return {"product_id": pid, "agent": "competitor_analyst", "metric": "listing_count", "value": 4293,
                "source_type": "marketplace", "verification": "observed", "confidence": 0.6,
                "source_url": "https://www.lazada.vn/catalog/?q=x", **kw}

    def test_observed_without_excerpt_rejected(self):
        pid = self.product()["product_id"]
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "observation", self.base(pid))

    def test_value_must_appear_in_excerpt(self):
        pid = self.product()["product_id"]
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "observation", self.base(pid, evidence_excerpt='Tìm thấy 4239 sản phẩm cho "x"'))

    def test_formatted_number_accepted(self):
        pid = self.product()["product_id"]
        r = self.st.append(self.run, "observation", self.base(pid, evidence_excerpt='Tìm thấy 4.293 sản phẩm cho "x"'))
        self.assertTrue(r["id"].startswith("obs_"))
        self.assertTrue(aph.value_in_excerpt(750000, "Giá: 750.000₫"))
        self.assertTrue(aph.value_in_excerpt("Còn hàng", "781.000 ₫ · còn hàng"))

    def test_reported_does_not_need_excerpt(self):
        pid = self.product()["product_id"]
        r = self.st.append(self.run, "observation", self.base(pid, verification="reported"))
        self.assertTrue(r["id"].startswith("obs_"))


class TestPerformance(Base):
    def test_list_scans_store_once(self):
        # Dữ liệu tổng hợp trong thư mục tạm, chỉ để đo tốc độ, không phải dữ liệu nghiên cứu.
        for i in range(2000):
            self.st._write_product({"product_id": f"prd_b{i:06d}", "canonical_name": f"sp {i}", "category": "smart_home",
                                    "function_key": f"bench-{i}", "aliases": []})
        path = self.st.records_path(self.run)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for k in range(50000):
                f.write(json.dumps({"id": f"dec_{k}", "kind": "decision", "run_id": self.run, "collected_at": "2026-11-24T00:00:00Z",
                                    "product_id": f"prd_b{k % 2000:06d}", "research_status": "watchlist"}) + "\n")
        import time
        t = time.time()
        rows = aph.list_products(self.st)
        self.assertEqual(len(rows), 2000)
        self.assertEqual(rows[0]["research_status"], "watchlist")
        self.assertLess(time.time() - t, 15)


class TestProfit(unittest.TestCase):
    def test_base_scenario_matches_hand_calc(self):
        r = aph.profit_sim(full_inputs(), BUSINESS)
        self.assertEqual(r["status"], "complete")
        b = r["scenarios"]["base"]
        # tính tay: landed 148000; fees 290000*18% = 52200; ads 29000; returns 5%*(148000*40%) = 2960
        # net = 290000-148000-52200-29000-0-4000-4000-2960-0 = 49840
        self.assertEqual(b["landed_cost"], 148000)
        self.assertEqual(b["platform_fees"], 52200)
        self.assertEqual(b["net_profit_per_unit"], 49840)
        self.assertEqual(b["net_margin_pct"], 17.2)
        self.assertEqual(b["breakeven_units"], 31)  # ceil(1500000/49840)
        self.assertEqual(b["test_capital"], 30 * 148000 + 1500000)
        self.assertTrue(b["fits_test_capital"])

    def test_conservative_is_worse_than_optimistic(self):
        s = aph.profit_sim(full_inputs(), BUSINESS)["scenarios"]
        self.assertLess(s["conservative"]["net_profit_per_unit"], s["base"]["net_profit_per_unit"])
        self.assertLess(s["base"]["net_profit_per_unit"], s["optimistic"]["net_profit_per_unit"])

    def test_missing_cost_is_incomplete(self):
        inp = full_inputs()
        del inp["unit_cost"]
        r = aph.profit_sim(inp, BUSINESS)
        self.assertEqual(r["status"], "incomplete")
        self.assertIn("unit_cost", r["missing"])
        self.assertNotIn("scenarios", r)

    def test_capital_limit(self):
        r = aph.profit_sim(full_inputs(moq={"conservative": 100, "base": 100, "optimistic": 100, "ref": "user:t"}), BUSINESS)
        self.assertFalse(r["scenarios"]["base"]["fits_test_capital"])

    def test_negative_net_has_no_breakeven(self):
        r = aph.profit_sim(full_inputs(ads_pct={"conservative": 60, "base": 60, "optimistic": 60, "ref": "user:t"}), BUSINESS)
        self.assertIsNone(r["scenarios"]["base"]["breakeven_units"])

    def test_unknown_ref_flagged(self):
        r = aph.profit_sim(full_inputs(unit_cost={"conservative": 1, "base": 1, "optimistic": 1, "ref": "obs_nope"}),
                           BUSINESS, known_ids=set(), assumption_ids=set())
        self.assertIn("unit_cost", r["unreferenced"])


class TestScoring(Base):
    def test_no_evidence_means_zero_points_and_cap_rules(self):
        pid = self.product()["product_id"]
        ev = aph.score(self.st, pid, {c: {"score": 10} for c in aph.cfg("scoring_weights")["criteria"]})
        self.assertEqual(ev["total"], 0)
        self.assertEqual(ev["data_confidence"], 0)
        self.assertTrue(ev["flags"])

    def test_cap_when_critical_unverified(self):
        pid = self.product()["product_id"]
        # bằng chứng cho mọi tiêu chí trừ profit (critical)
        for m, u in [("search_interest", "https://trends.google.com/a"), ("search_interest", "https://tiktok.com/csi"),
                     ("listing_count", "https://lazada.vn/a"), ("seller_count", "https://google.com/b"),
                     ("unit_cost", "https://1688.com/c"), ("content_signal", "https://tiktok.com/v")]:
            self.obs(pid, metric=m, url=u, series=[{"t": "2026-10-01", "v": 1}, {"t": "2026-11-01", "v": 2}])
        self.st.append(self.run, "supplier", {"product_id": pid, "name": "NCC", "platform": "web", "url": "https://ncc.vn",
                                             "quote_status": "listed", "verification": "observed",
                                             "price_tiers": [{"min_qty": 1, "price": 120000}], "price_basis": "wholesale_listed",
                                             "evidence_excerpt": "Giá sỉ từ 1 cái: 120.000đ"})
        self.st.append(self.run, "listing", {"product_id": pid, "platform": "lazada", "url": "https://lazada.vn/l1", "verification": "observed"})
        ev = self.obs(pid, metric="complaint_mention", url="https://lazada.vn/r1")["id"]
        self.st.append(self.run, "demand", {"product_id": pid, "type": "complaint", "theme": "mất kết nối", "evidence": [ev], "independent_sources": 3})
        # mô phỏng lợi nhuận đủ trường nhưng không có tham chiếu nguồn -> độ tin cậy profit thấp
        inp = {k: {sc: v[sc] for sc in aph.SCENARIOS} for k, v in full_inputs().items()}
        sim = aph.profit_sim(inp, BUSINESS)
        self.st.append(self.run, "profit", {"product_id": pid, **sim}, validate_kind=False)
        ev = aph.score(self.st, pid, {c: {"score": 9} for c in aph.cfg("scoring_weights")["criteria"]})
        self.assertIn("profit", ev["weak_critical"])
        self.assertGreater(ev["raw_total"], 74)
        self.assertIsNotNone(ev["cap_reason"])
        self.assertLessEqual(ev["total"], 74)
        self.assertEqual(aph.gate(ev)["suggested_research_status"], "research_more")

    def test_two_independent_scores_differ(self):
        pid = self.product()["product_id"]
        c1 = aph.compute_confidence(self.st, pid)["data_confidence"]
        self.obs(pid, metric="price", url="https://lazada.vn/x")
        self.obs(pid, metric="price", url="https://tuyaviet.com/y")
        c2 = aph.compute_confidence(self.st, pid)["data_confidence"]
        self.assertGreater(c2, c1)

    def test_stale_data_lowers_confidence(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="price", url="https://lazada.vn/x", observed_at="2026-11-20")
        self.obs(pid, metric="price", url="https://shop.vn/y", observed_at="2026-11-20")
        now = dt.datetime(2026, 11, 24, tzinfo=dt.timezone.utc)
        later = dt.datetime(2027, 5, 24, tzinfo=dt.timezone.utc)
        fresh = aph.compute_confidence(self.st, pid, now)["criteria"]["competition"]["confidence"]
        stale = aph.compute_confidence(self.st, pid, later)["criteria"]["competition"]["confidence"]
        self.assertLess(stale, fresh)

    def test_growth_needs_two_timepoints(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="search_interest", url="https://trends.google.com/a", observed_at="2026-11-20")
        self.obs(pid, metric="search_interest", url="https://tiktok.com/csi", observed_at="2026-11-20")
        one = aph.compute_confidence(self.st, pid)["criteria"]["growth"]
        self.assertEqual(one["timepoints"], 1)
        self.obs(pid, metric="search_interest", url="https://trends.google.com/a", observed_at="2026-10-20")
        two = aph.compute_confidence(self.st, pid)["criteria"]["growth"]
        self.assertGreater(two["confidence"], one["confidence"])


class TestStatusAndIndex(Base):
    def test_status_history_and_reindex(self):
        pid = self.product()["product_id"]
        self.st.append(self.run, "decision", {"product_id": pid, "business_status": "researching", "reason": "t"})
        self.st.append(self.run, "decision", {"product_id": pid, "research_status": "watchlist", "reason": "t"})
        self.assertEqual(self.st.status(pid), {"research_status": "watchlist", "business_status": "researching"})
        r = aph.reindex(self.st)
        self.assertEqual(r["products"], 1)
        self.assertEqual(r["records"], 2)

    def test_show_keeps_history(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="price", url="https://lazada.vn/x", platform="lazada", value=100)
        self.obs(pid, metric="price", url="https://lazada.vn/x", platform="lazada", value=90)
        s = aph.show(self.st, pid)
        self.assertEqual(s["history_points"]["price@lazada"], 2)
        self.assertEqual(s["current"]["price@lazada"]["value"], 90)

    def test_cli_roundtrip(self):
        out = aph.main(["--data", self.tmp.name, "list"])
        self.assertEqual(out, 0)


class TestProblems(Base):
    def problem(self, statement="Nhà bị ngập nước khi đi vắng do rò rỉ ống nước", key="water-leak-damage|home-away"):
        return self.st.append(self.run, "problem", {"statement": statement, "problem_key": key, "category": "smart_home",
                                                    "evidence": []})

    def test_problem_dedup(self):
        a = self.problem()
        b = self.problem(statement="Nha bi ngap nuoc khi di vang do ro ri ong nuoc", key="other-key")
        self.assertEqual(b["status"], "existing")
        self.assertEqual(b["id"], a["id"])

    def test_problem_level_evidence_flows_to_solution_product(self):
        prob = self.problem()
        self.st.append(self.run, "observation", {"problem_ref": prob["id"], "agent": "demand_analyst", "metric": "question_count",
                                                 "value": 12, "source_type": "forum", "verification": "observed", "confidence": 0.8,
                                                 "source_url": "https://forum.vn/t/1", "evidence_excerpt": "12 câu hỏi về nhà bị ngập"})
        pid = self.product()["product_id"]
        before = aph.compute_confidence(self.st, pid)["criteria"]["demand"]["evidence"]
        self.st.append(self.run, "solution", {"problem_ref": prob["id"], "product_id": pid, "fit": "partial", "rationale": "phát hiện sớm"})
        c = aph.compute_confidence(self.st, pid)
        self.assertEqual(before, 0)
        self.assertEqual(c["criteria"]["demand"]["evidence"], 1)
        self.assertIn(prob["id"], c["linked_problems"])

    def test_target_required_and_must_exist(self):
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "demand", {"type": "complaint", "theme": "x", "evidence": ["a"]})
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "demand", {"problem_ref": "pro_missing", "type": "complaint", "theme": "x", "evidence": ["a"]})

    def test_bundle_needs_components(self):
        prob = self.problem()
        pid = self.product()["product_id"]
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "solution", {"problem_ref": prob["id"], "product_id": pid, "fit": "bundle_component", "rationale": "r"})

    def test_list_problems(self):
        prob = self.problem()
        o1 = self.problem_obs(prob["id"], "https://forum-a.vn/t/1")
        self.problem_obs(prob["id"], "https://forum-b.vn/t/2")
        self.st.append(self.run, "demand", {"problem_ref": prob["id"], "type": "question", "theme": "lắp ở đâu", "evidence": [o1["id"]]})
        rows = aph.list_problems(self.st)
        self.assertEqual(rows[0]["demand_signals"], {"question": 1})
        self.assertEqual(rows[0]["evidence_count"], 2)
        self.assertEqual(rows[0]["independent_sources"], 2)

    def problem_obs(self, ref, url):
        return self.st.append(self.run, "observation", {"problem_ref": ref, "agent": "market_hunter", "metric": "question_mention",
                                                        "value": "lắp ở đâu", "source_type": "forum", "verification": "observed",
                                                        "confidence": 0.6, "source_url": url, "evidence_excerpt": "cảm biến nên lắp ở đâu ạ"})

    def test_evidence_ids_must_exist(self):
        prob = self.problem()
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "demand", {"problem_ref": prob["id"], "type": "complaint", "theme": "x",
                                                "evidence": ["{'error': 'observation cần product_id'}"]})

    def test_retraction_hides_record_and_frees_problem_key(self):
        bad = self.problem()
        self.st.retract(self.run, bad["id"], "evidence trỏ tới id lỗi khi ghi")
        self.assertEqual([p for p in self.st.records(kind="problem")], [])
        again = self.problem()
        self.assertNotEqual(again.get("status"), "existing")
        self.assertEqual(len(self.st.records(kind="problem", include_retracted=True)), 2)
        with self.assertRaises(aph.APHError):
            self.st.retract(self.run, bad["id"], "x")

    def test_problem_without_evidence_stays_candidate(self):
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "problem", {"statement": "Ổ cắm nóng chảy khi dùng bếp từ", "problem_key": "socket-overheat",
                                                 "category": "electrical_household", "evidence": [], "status": "validated"})


class TestDemandNotAttentionOnly(Base):
    def test_attention_only_is_capped(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="search_interest", url="https://trends.google.com/a")
        self.obs(pid, metric="content_signal", url="https://tiktok.com/v")
        d = aph.compute_confidence(self.st, pid)["criteria"]["demand"]
        self.assertTrue(d["attention_only"])
        self.assertLessEqual(d["confidence"], 0.3)
        ev = self.obs(pid, metric="question_count", url="https://lazada.vn/q1")["id"]
        self.st.append(self.run, "demand", {"product_id": pid, "type": "purchase_intent", "theme": "hỏi mua", "evidence": [ev],
                                            "independent_sources": 3})
        d2 = aph.compute_confidence(self.st, pid)["criteria"]["demand"]
        self.assertFalse(d2["attention_only"])
        self.assertGreater(d2["confidence"], 0.3)

    def test_listing_count_is_not_demand(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="listing_count", url="https://lazada.vn/a")
        self.assertEqual(aph.compute_confidence(self.st, pid)["criteria"]["demand"]["evidence"], 0)

    def test_evidence_classes(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="price", url="https://lazada.vn/a")
        self.obs(pid, metric="price", url="https://blog.vn/a", verification="reported")
        self.st.append(self.run, "observation", {"product_id": pid, "agent": "profit_analyst", "metric": "unit_cost", "value": 100,
                                                 "source_type": "user", "verification": "estimated", "confidence": 0.3,
                                                 "note": "ước tính", "range_low": 80, "range_high": 120})
        self.assertEqual(aph.compute_confidence(self.st, pid)["evidence_by_class"], {"direct": 1, "third_party": 1, "estimated": 1})


class TestFeeVerification(unittest.TestCase):
    A = {"tx": {"id": "tx", "platform": "shopee", "value": 6, "source_url": "https://banhang.shopee.vn/edu/article/21087",
                "source_date": "2026-05-14", "applies_to_categories": ["all"], "shop_type": "mall", "valid_until": "2027-01-24"},
         "fix_home": {"id": "fix_home", "platform": "shopee", "value": 9, "source_url": "https://banhang.shopee.vn/x",
                      "source_date": "2026-10-01", "applies_to_categories": ["Nhà cửa & Đời sống"], "shop_type": "mall"},
         "miss": {"id": "miss", "platform": "shopee", "value": None, "status": "missing"}}

    def inp(self, a="assumption:fix_home", b="assumption:tx"):
        return {"platform_fee_pct": {"base": 9, "ref": a}, "payment_fee_pct": {"base": 6, "ref": b}}

    def test_valid(self):
        self.assertEqual(aph.check_fee_refs(self.inp(), self.A, "shopee", "Nhà cửa & Đời sống", "mall", "2026-11-24"), [])

    def test_user_typed_fee_rejected(self):
        e = aph.check_fee_refs(self.inp(a="user:13%"), self.A, "shopee", "Nhà cửa & Đời sống", "mall", "2026-11-24")
        self.assertTrue(any("platform_fee_pct" in x for x in e))

    def test_wrong_category_rejected(self):
        e = aph.check_fee_refs(self.inp(), self.A, "shopee", "Thiết bị điện tử", "mall", "2026-11-24")
        self.assertTrue(any("ngành hàng" in x for x in e))

    def test_unknown_shop_type_rejected(self):
        self.assertTrue(aph.check_fee_refs(self.inp(), self.A, "shopee", "Nhà cửa & Đời sống", None, "2026-11-24"))

    def test_missing_and_expired_and_platform(self):
        self.assertTrue(aph.check_fee_refs(self.inp(a="assumption:miss"), self.A, "shopee", "Nhà cửa & Đời sống", "mall", "2026-11-24"))
        self.assertTrue(aph.check_fee_refs(self.inp(), self.A, "shopee", "Nhà cửa & Đời sống", "mall", "2027-02-01"))
        self.assertTrue(aph.check_fee_refs(self.inp(), self.A, "tiktok_shop", "Nhà cửa & Đời sống", "mall", "2026-11-24"))

    def test_profit_incomplete_when_fees_unverified(self):
        r = aph.profit_sim(full_inputs(), BUSINESS, fee_errors=["platform_fee_pct: x"])
        self.assertEqual(r["status"], "incomplete")
        self.assertNotIn("scenarios", r)


class TestCoverage(Base):
    def test_shopee_gap_reported(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="price", url="https://www.lazada.vn/a", platform="lazada")
        self.st.append(self.run, "access", {"url": "https://shopee.vn/search?keyword=x", "status": "captcha"})
        c = aph.coverage(self.st, pid)
        self.assertEqual(c["needs"]["vn_price"]["platforms"], ["lazada"])
        self.assertFalse(c["needs"]["vn_price"]["covered"])
        self.assertFalse(c["needs"]["vn_price"]["shopee_present"])
        self.assertIn("shopee", c["needs"]["vn_price"]["next_sources"])
        self.assertEqual(c["blocked_sources_seen"], {"shopee.vn": "captcha"})


if __name__ == "__main__":
    unittest.main()


def weekly(vals, start="2025-11-23"):
    d0 = __import__("datetime").date.fromisoformat(start)
    return [{"t": (d0 + __import__("datetime").timedelta(days=7 * i)).isoformat(), "v": v} for i, v in enumerate(vals)]


class TestTrendStage(Base):
    def test_growing_uses_median_not_spike(self):
        flat = [20] * 41 + [27] * 12
        self.assertEqual(aph.trend_indicator(weekly(flat))["stage"], "growing")
        spike = [20] * 30 + [100] + [20] * 22
        self.assertEqual(aph.trend_indicator(weekly(spike))["stage"], "stable")

    def test_declining_emerging_lowvolume(self):
        self.assertEqual(aph.trend_indicator(weekly([40] * 41 + [20] * 12))["stage"], "declining")
        self.assertEqual(aph.trend_indicator(weekly([0, 1] * 14 + [5] * 13 + [30] * 12))["stage"], "emerging")
        low = aph.trend_indicator(weekly([0, 0, 5] * 17 + [0, 0]))
        self.assertEqual((low["stage"], low["reason"]), ("unknown", "low_volume"))
        self.assertEqual(aph.trend_indicator(weekly([30] * 10))["reason"], "too_few_points")
        noisy = aph.trend_indicator(weekly([0, 0, 12] * 13 + [3, 4] * 7))  # trung vị nhỏ hơn 10: nhiễu, không phải tăng
        self.assertEqual(noisy["reason"], "low_volume")
        self.assertEqual(aph.trend_indicator(weekly([20] * 41 + [40] * 12))["stage"], "trending")

    def test_needs_two_sources_to_conclude(self):
        pid = self.product()["product_id"]
        up = weekly([20] * 41 + [27] * 12)
        self.obs(pid, metric="search_interest", url="https://trends.google.com/a", series=up, value=30, evidence_excerpt="tuần cuối 30")
        one = aph.trend_assess(self.st, pid)
        self.assertEqual(one["stage"], "unknown")
        self.assertIn("Chỉ 1 nguồn", one["basis"])
        self.obs(pid, metric="csi_popularity", url="https://www.tiktok.com/csi", series=up, value=30, evidence_excerpt="độ phổ biến 30")
        two = aph.trend_assess(self.st, pid)
        self.assertEqual(two["stage"], "growing")
        self.assertEqual(len(two["evidence"]), 2)

    def test_conflicting_sources_stay_unknown(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="search_interest", url="https://trends.google.com/a", series=weekly([20] * 41 + [27] * 12), value=30, evidence_excerpt="tuần cuối 30")
        self.obs(pid, metric="csi_popularity", url="https://www.tiktok.com/csi", series=weekly([40] * 41 + [20] * 12), value=20, evidence_excerpt="độ phổ biến 20")
        r = aph.trend_assess(self.st, pid)
        self.assertEqual(r["stage"], "unknown")
        self.assertIn("mâu thuẫn", r["basis"])

    def test_absolute_series_scaled_and_gaps_dropped(self):
        # Chuỗi số tuyệt đối (TikTok CSI cộng theo tuần) cho cùng kết quả như chuỗi 0–100 cùng hình dạng.
        self.assertEqual(aph.trend_indicator(weekly([2000] * 41 + [4000] * 12))["stage"], "trending")
        self.assertEqual(aph.trend_indicator(weekly([84] * 13 + [218] * 12))["stage"],
                         aph.trend_indicator(weekly([8400] * 13 + [21800] * 12))["stage"])
        # Sau khi quy về 0–100, chuỗi gần như phẳng với vài đột biến lớn bị coi là ít dữ liệu (chưa quy đổi thì 14 > 10 sẽ lọt).
        spiky = aph.trend_indicator(weekly([14, 14, 14, 14, 2000] * 5))
        self.assertEqual(spiky["reason"], "low_volume")
        # Tuần mất dữ liệu (v = None) bị bỏ, không tính như 0; thiếu tuần thì không kết luận.
        gap = weekly([12000] * 2 + [None] * 7 + [12000] * 16)
        self.assertEqual(aph.trend_indicator(gap)["reason"], "too_few_points")

    def test_same_direction_different_level_takes_conservative(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="search_interest", url="https://trends.google.com/a", series=weekly([20] * 41 + [27] * 12), value=27, evidence_excerpt="tuần cuối 27")
        self.obs(pid, metric="csi_popularity", url="https://www.tiktok.com/csi", series=weekly([20] * 41 + [40] * 12), value=40, evidence_excerpt="độ phổ biến 40")
        r = aph.trend_assess(self.st, pid)
        self.assertEqual(r["stage"], "growing")
        self.assertIn("2 nguồn cùng hướng", r["basis"])


class TestSupplierRules(Base):
    def sup(self, pid, **kw):
        d = {"product_id": pid, "name": "NCC A", "platform": "web", "url": "https://ncc.example/sp",
             "quote_status": "listed", "verification": "observed", "market": "VN", **kw}
        return self.st.append(self.run, "supplier", d)

    def test_price_must_be_in_excerpt_and_basis_required(self):
        pid = self.product()["product_id"]
        ok = self.sup(pid, price_tiers=[{"min_qty": 1, "price": 1450000}], price_basis="wholesale_listed",
                      evidence_excerpt="KHÓA CỬA VÂN TAY TTLOCK-17HT 1,450,000 ₫ ( Đã gồm VAT)")
        self.assertTrue(ok["id"].startswith("sup_") or ok["id"])
        with self.assertRaises(aph.APHError):  # giá không có trong đoạn trích
            self.sup(pid, price_tiers=[{"min_qty": 1, "price": 990000}], price_basis="wholesale_listed",
                     evidence_excerpt="KHÓA CỬA VÂN TAY TTLOCK-17HT 1,450,000 ₫")
        with self.assertRaises(aph.APHError):  # thiếu price_basis: không phân biệt giá lẻ và giá nhập
            self.sup(pid, price_tiers=[{"min_qty": 1, "price": 1450000}], evidence_excerpt="TTLOCK-17HT 1,450,000 ₫")
        with self.assertRaises(aph.APHError):  # observed mà không có đoạn trích
            self.sup(pid, quote_status="need_quote")

    def test_listed_needs_price_need_quote_does_not(self):
        pid = self.product()["product_id"]
        with self.assertRaises(aph.APHError):
            self.sup(pid, evidence_excerpt="Liên hệ để nhận chính sách đại lý")
        r = self.sup(pid, quote_status="need_quote", evidence_excerpt="Nhận chính sách đại lý")
        self.assertEqual(r["quote_status"], "need_quote")

    def test_coverage_separates_retail_from_cost_price(self):
        pid = self.product()["product_id"]
        self.sup(pid, quote_status="need_quote", price_tiers=[{"min_qty": 1, "price": 455000}], price_basis="retail_listed",
                 evidence_excerpt="Giá hiện tại là: 455.000₫.")
        c = aph.coverage(self.st, pid)["needs"]["supply"]
        self.assertFalse(c["cost_price_known"])
        self.assertEqual(len(c["need_quote"]), 1)
        w = self.sup(pid, price_tiers=[{"min_qty": 1, "price": 290000}], price_basis="wholesale_listed",
                     evidence_excerpt="Giá sỉ 290,000đ")
        c = aph.coverage(self.st, pid)["needs"]["supply"]
        self.assertTrue(c["cost_price_known"])
        self.assertEqual(c["cost_price_suppliers"], [w["id"]])


class TestVendorReview(Base):
    def crit(self, **over):
        site = {"status": "stated", "source_url": "https://ncc.example/lien-he", "evidence_excerpt": "Công ty TNHH NCC, MST 0101234567"}
        c = {k: dict(site) for k in aph.VENDOR_CRITERIA}
        c.update(over)
        return c

    def review(self, **kw):
        d = {"vendor_key": "ncc.example", "vendor_name": "NCC", "criteria": self.crit(), "market": "VN", **kw}
        return self.st.append(self.run, "vendor_review", d)

    def test_needs_all_six_criteria_and_evidence(self):
        c = self.crit(); del c["invoice"]
        with self.assertRaises(aph.APHError):
            self.review(criteria=c)
        with self.assertRaises(aph.APHError):  # stated mà không có đoạn trích
            self.review(criteria=self.crit(warranty={"status": "stated", "source_url": "https://ncc.example/bh"}))
        with self.assertRaises(aph.APHError):  # verified phải từ nguồn độc lập, không phải trang của NCC
            self.review(criteria=self.crit(legal_entity={"status": "verified", "source_url": "https://ncc.example/gioi-thieu",
                                                         "evidence_excerpt": "MST 0101234567 đang hoạt động"}))
        with self.assertRaises(aph.APHError):  # trang khác của chính NCC (vendor_domains) cũng không tính là độc lập
            self.review(vendor_domains=["store.ncc.example"],
                        criteria=self.crit(legal_entity={"status": "verified", "source_url": "https://store.ncc.example/chinh-sach",
                                                         "evidence_excerpt": "GPĐKKD số 0101234567"}))
        with self.assertRaises(aph.APHError):  # supplier_refs phải có trong kho
            self.review(supplier_refs=["sup_khongco"])

    def test_scoring_levels(self):
        self_stated = aph.vendor_assessment(self.review())
        self.assertEqual((self_stated["score"], self_stated["level"]), (6, "medium"))
        self.assertFalse(self_stated["legal_entity_confirmed"])
        verified = {"status": "verified", "source_url": "https://masothue.example/0101234567", "evidence_excerpt": "0101234567 - Đang hoạt động"}
        good = aph.vendor_assessment(self.review(criteria=self.crit(legal_entity=verified, address=verified,
                                                                     invoice={"status": "user_input", "value": "Có xuất VAT (anh Khoa xác nhận)"})))
        self.assertEqual((good["score"], good["level"]), (9, "high"))
        gaps = aph.vendor_assessment(self.review(criteria=self.crit(
            legal_entity={"status": "missing", "note": "không thấy MST"}, invoice={"status": "missing", "note": "không ghi"})))
        self.assertEqual(gaps["level"], "low")
        self.assertEqual(gaps["ask_vendor"], ["Pháp nhân", "Xuất hóa đơn"])
        self.assertEqual(aph.list_vendors(self.st)[0]["review_id"], gaps["review_id"])  # bản mới nhất của mỗi NCC


class TestProfitAnalyst(Base):
    """Agent 06: giá bán thị trường, ghép đầu vào từ kho, giá nhập tối đa."""

    def listing(self, pid, price, **kw):
        # mỗi lần gọi là một tin đăng khác nhau (URL riêng): price_stats chỉ tính mỗi tin một lần
        self._n = getattr(self, "_n", 0) + 1
        d = {"product_id": pid, "platform": "lazada", "url": f"https://lazada.vn/x{self._n}", "verification": "observed",
             "price_vnd": price, "evidence_excerpt": f"Cảm biến cửa Zigbee ₫{price:,}", **kw}
        return self.st.append(self.run, "listing", d)

    def set_assumptions(self, extra=()):
        base = [{"id": "shopee_total_fee_pct_user", "value": 27, "platform": "shopee", "applies_to_categories": ["all"],
                 "shop_type": "normal", "source_url": "user:test", "source_date": "2026-11-24", "valid_until": "2099-01-01",
                 "verification": "user_input"}]
        p = pathlib.Path(self.tmp.name) / "assumptions" / "assumptions.json"
        p.write_text(json.dumps({"assumptions": base + list(extra)}, ensure_ascii=False), encoding="utf-8")

    def cost_profile(self):
        v = lambda c, b, o: {"conservative": c, "base": b, "optimistic": o}
        mk = lambda f, val: {"id": f"cost_{f}", "value": val, "verification": "user_input", "source_url": "user:test",
                             "source_date": "2026-11-24"}
        return [mk("inbound_shipping", 5000), mk("ads_pct", v(15, 10, 5)), mk("seller_shipping", 0), mk("packaging", 3000),
                mk("ops_per_unit", 2000), mk("return_rate_pct", v(8, 5, 3)), mk("return_loss_pct", 40),
                mk("other_per_unit", 0), mk("fixed_test_cost", 1000000)]

    def test_listing_price_must_be_in_excerpt(self):
        pid = self.product()["product_id"]
        with self.assertRaises(aph.APHError):
            self.listing(pid, 250000, evidence_excerpt="Cảm biến cửa Zigbee ₫199.000")

    def test_price_stats_quantiles_and_minimum(self):
        pid = self.product()["product_id"]
        self.listing(pid, 200000); self.listing(pid, 300000)
        self.assertEqual(aph.price_stats(self.st, pid)["status"], "insufficient")
        self.listing(pid, 400000); self.listing(pid, 500000)
        s = aph.price_stats(self.st, pid)
        self.assertEqual((s["p25"], s["median"], s["p75"]), (275000, 350000, 425000))
        for p in (900000, 950000, 1000000):
            self.listing(pid, p, variant_key="hang-x")
        self.assertEqual(aph.price_stats(self.st, pid, variant="hang-x")["median"], 950000)

    def test_build_inputs_lists_missing_without_inventing(self):
        pid = self.product()["product_id"]
        for p in (200000, 300000, 400000):
            self.listing(pid, p)
        self.set_assumptions()
        b = aph.build_profit_inputs(self.st, pid, "shopee")
        self.assertEqual(b["inputs"]["sell_price"]["base"], 300000)
        self.assertEqual(b["inputs"]["payment_fee_pct"]["base"], 0)
        self.assertIn("unit_cost", b["missing"]); self.assertIn("ads_pct", b["missing"])
        self.assertNotIn("ads_pct", b["inputs"])
        # giá bán lẻ của hãng không được dùng làm giá nhập
        self.st.append(self.run, "supplier", {"product_id": pid, "name": "Hãng", "platform": "web", "url": "https://h.example/sp",
                                              "quote_status": "need_quote", "verification": "observed", "price_basis": "retail_listed",
                                              "price_tiers": [{"min_qty": 1, "price": 455000}], "evidence_excerpt": "Giá 455.000₫"})
        self.assertIn("unit_cost", aph.build_profit_inputs(self.st, pid, "shopee")["missing"])

    def test_full_chain_and_max_cost_round_trip(self):
        pid = self.product()["product_id"]
        for p in (250000, 290000, 320000):
            self.listing(pid, p)
        self.set_assumptions(self.cost_profile())
        self.st.append(self.run, "supplier", {"product_id": pid, "name": "Đại lý", "platform": "user", "url": "user:bang-gia",
                                              "quote_status": "quoted", "verification": "user_input", "price_basis": "dealer_quote",
                                              "price_tiers": [{"min_qty": 10, "price": 120000}]})
        b = aph.build_profit_inputs(self.st, pid, "shopee")
        self.assertEqual(b["missing"], [])
        amap = {a["id"]: a for a in self.st.assumptions()}
        self.assertEqual(aph.check_fee_refs(b["inputs"], amap, "shopee", None, "normal", "2026-11-24"), [])
        known = {r["id"] for r in self.st.records(pid)}
        r = aph.profit_sim(b["inputs"], BUSINESS, known, set(amap))
        self.assertEqual(r["status"], "complete"); self.assertEqual(r["unreferenced"], [])
        # tính tay kịch bản cơ sở: p=290000, landed=125000, fees 27% = 78300, ads 10% = 29000,
        # returns 5%*(125000*40%) = 2500 -> net = 290000-125000-78300-29000-0-3000-2000-2500 = 50200
        self.assertEqual(r["scenarios"]["base"]["net_profit_per_unit"], 50200)
        self.assertEqual(r["scenarios"]["base"]["test_capital"], 10 * 125000 + 1000000)
        mc = aph.max_unit_cost(b["inputs"], (10, 20))
        self.assertEqual(mc["status"], "complete")
        for sc in aph.SCENARIOS:
            for m in (10, 20):
                u = mc["scenarios"][sc][f"max_unit_cost_at_{m}pct"]
                chk = aph.profit_sim({**b["inputs"], "unit_cost": {s: u for s in aph.SCENARIOS} | {"ref": "user:t"}}, BUSINESS)
                self.assertAlmostEqual(chk["scenarios"][sc]["net_margin_pct"], m, delta=0.1)

    def test_max_cost_incomplete_and_not_viable(self):
        self.assertEqual(aph.max_unit_cost({"sell_price": 100000})["status"], "incomplete")  # thiếu phí sàn
        fee = {"base": 27, "conservative": 27, "optimistic": 27, "ref": "user:t"}
        zero = {"base": 0, "conservative": 0, "optimistic": 0, "ref": "user:t"}
        part = aph.max_unit_cost({"sell_price": {"conservative": 200000, "base": 200000, "optimistic": 200000, "ref": "user:t"},
                                  "platform_fee_pct": fee, "payment_fee_pct": zero}, (10, 20))
        self.assertEqual(part["status"], "partial")
        self.assertIn("ads_pct", part["excluded_costs"])
        # trần: 200000 * (1 - 27% - 10%) = 126000; * (1 - 27% - 20%) = 106000
        self.assertEqual(part["scenarios"]["base"]["max_unit_cost_at_10pct"], 126000)
        self.assertEqual(part["scenarios"]["base"]["max_unit_cost_at_20pct"], 106000)
        d = aph.discount_needed(part, 200000, "sup_x")
        self.assertEqual(d["base"]["min_discount_pct_at_10pct"], 37.0)  # 1 - 126000/200000
        self.assertEqual(aph.max_unit_cost({"sell_price": 1, "platform_fee_pct": fee, "payment_fee_pct": zero}, strict=True)["status"],
                         "incomplete")
        inp = full_inputs(ads_pct={"conservative": 70, "base": 70, "optimistic": 70, "ref": "user:t"})
        self.assertIsNone(aph.max_unit_cost(inp, (10,))["scenarios"]["base"]["max_unit_cost_at_10pct"])


class Agent07Base(Base):
    """Hàm dựng dữ liệu kiểm thử cho Agent 07 (thư mục tạm, chỉ để kiểm tra logic)."""
    V = "hang-a-wifi"
    TODAY = dt.date.today()

    def day(self, n):
        return (self.TODAY - dt.timedelta(days=n)).isoformat()

    def listing(self, pid, price, shop, platform="lazada", variant=None, sold=None, **kw):
        d = {"product_id": pid, "platform": platform, "url": f"https://{platform}.example/{shop}", "verification": "observed",
             "price_vnd": price, "shop_name": shop, "evidence_excerpt": f"Cảm biến Hãng A WiFi ₫{price:,}", "variant_key": variant or self.V, **kw}
        if sold is not None:
            d["sold_label"] = f"{sold} Đã bán"
        return self.st.append(self.run, "listing", d)

    def sold_obs(self, pid, value, days_ago, platform="shopee"):
        return self.obs(pid, metric="sold_label", url=f"https://{platform}.example/sp", value=value, platform=platform,
                        observed_at=self.day(days_ago), variant_key=self.V, evidence_excerpt=f"{value} đã bán")

    def trend(self, pid, stage="growing"):
        a = self.obs(pid, metric="search_interest", url="https://trends.google.com/a", value=30, evidence_excerpt="tuần cuối 30",
                     observed_at=self.day(0))
        b = self.obs(pid, metric="csi_popularity", url="https://www.tiktok.com/csi", value=30, evidence_excerpt="độ phổ biến 30",
                     observed_at=self.day(0))
        return self.st.append(self.run, "trend", {"product_id": pid, "stage": stage, "basis": "2 nguồn (test)", "evidence": [a["id"], b["id"]]})

    def supplier(self, pid, basis="dealer_quote", price=150000, variant="__V__", **kw):
        d = {"product_id": pid, "name": "Hãng A", "platform": "user", "url": "https://hang-a.example/sp", "quote_status": "quoted",
             "verification": "user_input", "market": "VN", "price_basis": basis, "price_tiers": [{"min_qty": 10, "price": price}], **kw}
        if variant:
            d["variant_key"] = self.V if variant == "__V__" else variant
        return self.st.append(self.run, "supplier", d)

    def review(self, sup_ids, **over):
        ver = {"status": "verified", "source_url": "https://trangvang.example/a", "evidence_excerpt": "Công ty CP Hãng A, MST 0101234567"}
        c = {k: {"status": "stated", "source_url": "https://hang-a.example/cs", "evidence_excerpt": "Bảo hành 12 tháng, xuất VAT"}
             for k in aph.VENDOR_CRITERIA}
        c["legal_entity"] = dict(ver); c["address"] = dict(ver)
        c["invoice"] = {"status": "user_input", "value": "Có xuất VAT"}
        c["moq_payment"] = {"status": "user_input", "value": "MOQ 10, chuyển khoản"}
        c.update(over)
        return self.st.append(self.run, "vendor_review", {"vendor_key": "hang-a.example", "vendor_name": "Hãng A", "market": "VN",
                                                          "supplier_refs": sup_ids, "criteria": c})

    def profit(self, pid, sup_id):
        inp = full_inputs(unit_cost={"conservative": 150000, "base": 140000, "optimistic": 130000, "ref": sup_id})
        res = aph.profit_sim(inp, BUSINESS)
        return self.st.append(self.run, "profit", {"product_id": pid, "inputs": inp, "platform": "shopee", "variant": self.V, **res},
                              validate_kind=False)

    def max_cost(self, pid, retail_id=None, ceiling=180000, d10=40.0):
        rec = {"product_id": pid, "status": "partial", "platform": "shopee", "variant": self.V, "targets_pct": [10, 20],
               "scenarios": {sc: {"sell_price": 290000, "max_unit_cost_at_10pct": ceiling, "max_unit_cost_at_20pct": ceiling - 29000}
                             for sc in aph.SCENARIOS}, "excluded_costs": ["ads_pct", "packaging"]}
        if retail_id:
            rec["discount_needed"] = {"retail_price": 300000, "ref": retail_id,
                                      **{sc: {"min_discount_pct_at_10pct": d10, "min_discount_pct_at_20pct": d10 + 9} for sc in aph.SCENARIOS}}
        return self.st.append(self.run, "max_cost", rec, validate_kind=False)

    def strong(self, *, real_cost=True, trend="growing", demand="strong", tag="1"):
        pid = self.product(name=f"Cảm biến Hãng A {tag}", fk=f"door-sensor|wifi-{tag}", force_new=True)["product_id"]
        first = None
        for price, shop, plat, sold, delta in [(270000, "Shop 1", "lazada", 120, 60), (290000, "Shop 2", "lazada", 90, 50), (300000, "Shop 3", "shopee", 110, 40)]:
            d = delta if demand == "strong" else (1 if shop == "Shop 2" else 0)
            a = self.listing(pid, price, shop, plat, sold=sold, observed_at=self.day(21))
            self.listing(pid, price, shop, plat, sold=sold + d, observed_at=self.day(0), weaknesses=["pin yếu"] if shop == "Shop 1" else [])
            first = first or a
        self.profile(pid, [first["id"]])
        if trend:
            self.trend(pid, trend)
        if real_cost:
            sup = self.supplier(pid)
            self.review([sup["id"]])
            self.profit(pid, sup["id"])
        else:
            retail = self.supplier(pid, basis="retail_listed", price=300000, quote_status="need_quote")
            self.review([retail["id"]])
            self.max_cost(pid, retail["id"])
        return pid

    def profile(self, pid, ev, **kw):
        d = {"product_id": pid, "variant_key": self.V, "brand": "Hãng A", "model_name": "Cảm biến cửa Hãng A WiFi", "protocol": "wifi",
             "evidence": ev, "evidence_excerpt": "Cảm biến Hãng A WiFi", **kw}
        return self.st.append(self.run, "variant_profile", d)

    def ev(self, pid, variant=None, **kw):
        return aph.evaluate(self.st, pid, variant or self.V, **kw)



class TestOpportunityEvaluator(Agent07Base):
    """Agent 07: chấm điểm v2 theo model, Risk Gates, so sánh giữa các lần đánh giá."""

    # Test 1
    def test_go_when_all_gates_pass(self):
        e = self.ev(self.strong())
        self.assertEqual(e["status"], "GO", e["conditions"])
        self.assertTrue(all(g["status"] == "pass" for g in e["gates"].values()))
        self.assertGreaterEqual(e["total"], 75); self.assertEqual(e["weight_coverage"], 100)
        self.assertEqual(e["suggested_research_status"], "qualified_opportunity")

    # Test 2
    def test_no_real_cost_never_claims_profit(self):
        e = self.ev(self.strong(real_cost=False))
        f = e["groups"]["financial"]
        self.assertEqual(f["basis"], "estimated"); self.assertLessEqual(f["score"], 50)
        self.assertIn("Chưa có giá nhập thực tế", f["reason"])
        self.assertEqual(e["gates"]["financial"]["status"], "insufficient")
        self.assertEqual(e["status"], "CONDITIONAL_GO")
        # Giá lẻ không phải giá nhập: điểm NCC không có thưởng giá
        self.assertIn("giá bán lẻ không phải giá vốn", e["groups"]["supplier"]["reason"])

    # Test 3 + yêu cầu C
    def test_trend_unknown_is_null_and_not_required(self):
        e = self.ev(self.strong(trend="unknown"))
        t = e["groups"]["trend"]
        self.assertIsNone(t["score"]); self.assertEqual(t["status"], "no_data"); self.assertEqual(t["stage"], "unknown")
        self.assertEqual(e["weight_coverage"], 85)
        self.assertEqual(e["status"], "GO")  # không bắt buộc tăng trưởng
        self.assertNotIn("trend", e["gates"])
        s = self.ev(self.strong(trend="stable", tag="2"))
        self.assertEqual(s["groups"]["trend"]["score"], 65); self.assertEqual(s["status"], "GO")

    # Test 4
    def test_unverified_vendor_shows_supply_risk(self):
        pid = self.strong()
        sup = self.st.records(pid, "supplier")[0]
        self.review([sup["id"]], legal_entity={"status": "stated", "source_url": "https://hang-a.example/lh", "evidence_excerpt": "Hãng A, Hà Nội"},
                    warranty={"status": "missing", "note": "không thấy chính sách bảo hành"})
        e = self.ev(pid)
        self.assertEqual(e["gates"]["supplier"]["status"], "insufficient")
        self.assertIn("pháp nhân chưa đối chiếu", e["gates"]["supplier"]["reason"])
        self.assertIn("bảo hành", e["gates"]["supplier"]["reason"])
        self.assertTrue(any("Rủi ro nguồn cung" in r for r in e["risks"]))
        self.assertNotEqual(e["status"], "GO")

    # Test 5
    def test_weak_demand_blocks_go(self):
        e = self.ev(self.strong(demand="weak"))
        self.assertEqual(e["gates"]["demand"]["status"], "fail")
        self.assertEqual(e["status"], "NO_GO")
        self.assertGreater(e["groups"]["financial"]["score"], 50)

    # Test 6
    def test_sparse_data_need_more_data_without_inventing(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="search_interest", url="https://trends.google.com/x", value=20, evidence_excerpt="giá trị 20")
        e = self.ev(pid, "zigbee-x")
        self.assertEqual(e["status"], "NEED_MORE_DATA")
        for g in ("trend", "competition", "supplier", "financial"):
            self.assertIsNone(e["groups"][g]["score"], g)
        self.assertLessEqual(e["groups"]["demand"]["score"], 40)  # chỉ tín hiệu chú ý
        self.assertEqual(e["weight_coverage"], 25)
        self.assertTrue(e["next_actions"])
        self.assertEqual(aph.evaluate(self.st, pid, None)["gates"]["identity"]["status"], "insufficient")

    # Test 7
    def test_two_evaluations_compare_and_history_kept(self):
        pid = self.strong(real_cost=False)
        first = self.st.append(self.run, "evaluation", self.ev(pid))
        sup = self.supplier(pid)
        self.review([sup["id"]])
        self.profit(pid, sup["id"])
        second = self.st.append(self.run, "evaluation", self.ev(pid))
        d = aph.eval_diff(self.st, pid, self.V)
        self.assertEqual((d["from"]["id"], d["to"]["id"]), (first["id"], second["id"]))
        self.assertEqual(d["status"], {"from": "CONDITIONAL_GO", "to": "GO"})
        self.assertIn("financial", d["groups_changed"])
        self.assertIn("profit", {x["kind"] for x in d["inputs_added"]})
        self.assertEqual(d["gates_changed"]["financial"]["to"], "pass")
        stored = [r for r in self.st.records(pid, "evaluation")]
        self.assertEqual(stored[0], first)  # bản cũ không bị sửa

    # Yêu cầu A
    def test_coverage_below_80_blocks_go(self):
        pid = self.strong(trend="unknown")
        orig = aph.cfg
        def patched(name):
            c = orig(name)
            if name == "scoring_weights_v2":
                c = json.loads(json.dumps(c)); c["groups"]["trend"]["weight"] = 30; c["groups"]["demand"]["weight"] = 10
            return c
        aph.cfg = patched
        try:
            e = self.ev(pid)
        finally:
            aph.cfg = orig
        self.assertEqual(e["weight_coverage"], 70)
        self.assertGreaterEqual(e["total"], 75)
        self.assertEqual(e["status"], "CONDITIONAL_GO")
        self.assertTrue(any("weight_coverage" in c for c in e["conditions"]))

    # Yêu cầu B
    def test_more_competitors_is_not_better(self):
        few = self.product(name="SP ít đối thủ", fk="a|x")["product_id"]
        many = self.product(name="Hàng khác hẳn", fk="b|y", force_new=True)["product_id"]
        for i in range(3):
            self.listing(few, 280000 + i * 10000, f"Shop {i}")
        for i in range(20):
            self.listing(many, 280000 + (i % 3) * 10000, f"Shop {i}")
        a, b = self.ev(few)["groups"]["competition"], self.ev(many)["groups"]["competition"]
        self.assertGreater(a["score"], b["score"])
        self.assertLessEqual(a["score"], 70)  # chưa có bằng chứng khác biệt
        self.assertIn("chưa có bằng chứng khả năng khác biệt", a["reason"])
        self.assertIn(a["band"], [lbl for _, lbl in aph.cfg("scoring_weights_v2")["competition"]["bands"]])

    def test_undercut_and_incumbent_lower_competition(self):
        pid = self.product()["product_id"]
        for price, shop in [(150000, "Nhà Phân Phối A"), (300000, "Official Store"), (310000, "Shop X"), (305000, "Shop Y")]:
            self.listing(pid, price, shop)
        c = self.ev(pid)["groups"]["competition"]
        self.assertEqual(c["components"]["price"], 35)
        self.assertEqual(c["components"]["incumbent"], 35)

    # Yêu cầu D
    def test_identity_excludes_other_models_and_unassigned(self):
        pid = self.strong()
        for p in (90000, 95000, 99000):
            self.listing(pid, p, "Shop Z", variant="hang-b-zigbee", sold=999)
        un = self.supplier(pid, variant=None, name="NCC chưa rõ model", price=50000)
        e = self.ev(pid)
        self.assertEqual(e["groups"]["competition"]["price"]["min"], 270000)
        self.assertEqual(e["groups"]["demand"]["velocity"]["listings_measured"], 3)
        self.assertEqual(e["groups"]["demand"]["presence"]["listings"], 3)
        self.assertGreaterEqual(e["identity"]["other_model"], 3)
        self.assertIn(un["id"], e["identity"]["unassigned_ids"])
        self.assertEqual(e["gates"]["identity"]["status"], "pass")  # đã có NCC đúng model
        z = self.ev(pid, "hang-b-zigbee")
        self.assertEqual(z["gates"]["identity"]["status"], "insufficient")
        self.assertIn("NCC chưa xác định", z["gates"]["identity"]["reason"])
        self.assertNotEqual(z["status"], "GO")

    def test_identity_wide_price_spread(self):
        pid = self.product()["product_id"]
        for p in (700000, 900000, 1400000, 2500000):
            self.listing(pid, p, f"S{p}")
        g = self.ev(pid)["gates"]["identity"]
        self.assertEqual(g["status"], "insufficient"); self.assertIn("gộp nhiều mẫu", g["reason"])

    def test_variant_link_needs_verbatim_excerpt(self):
        pid = self.product()["product_id"]
        sup = self.supplier(pid, variant=None, name="Cảm biến cửa Wifi Hãng A Thế Hệ Mới")
        base = {"product_id": pid, "variant_key": self.V, "target": sup["id"], "reason": "Tên sản phẩm ghi WiFi Hãng A"}
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "variant_link", {**base, "evidence_excerpt": "Zigbee Hãng A"})
        lis = self.listing(pid, 300000, "Shop 1", variant="hang-b")
        with self.assertRaises(aph.APHError):  # bản ghi đã có model khác
            self.st.append(self.run, "variant_link", {**base, "target": lis["id"], "evidence_excerpt": "Hãng A WiFi"})
        self.st.append(self.run, "variant_link", {**base, "evidence_excerpt": "Wifi Hãng A"})
        self.assertEqual(aph.record_variant(sup, aph.variant_links(self.st.records())), self.V)
        b = aph.build_profit_inputs(self.st, pid, "shopee", self.V)
        self.assertEqual(b["inputs"]["unit_cost"]["ref"], sup["id"])
        self.assertIn("unit_cost", aph.build_profit_inputs(self.st, pid, "shopee", "hang-b")["missing"])

    def test_ai_judgment_clamped_and_needs_evidence(self):
        pid = self.strong()
        base = self.ev(pid)["groups"]["competition"]["score"]
        lid = self.st.records(pid, "listing")[0]["id"]
        e = self.ev(pid, judgments={"competition": {"score": 100, "reason": "Đối thủ chủ yếu ảnh kém", "evidence": [lid]}})
        c = e["groups"]["competition"]
        self.assertEqual(c["basis"], "ai_judgment"); self.assertEqual(c["tool_score"], base)
        self.assertLessEqual(c["score"], min(base + 10, c.get("cap") or 100))
        with self.assertRaises(aph.APHError):
            self.ev(pid, judgments={"financial": {"score": 90, "reason": "lãi tốt lắm", "evidence": [lid]}})
        with self.assertRaises(aph.APHError):
            self.ev(pid, judgments={"demand": {"score": 90, "reason": "nhu cầu cao", "evidence": ["obs_khongco"]}})

    def test_cli_evaluate_writes_schema_valid_record_and_report(self):
        pid = self.strong()
        out = pathlib.Path(self.tmp.name) / "r.md"
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(aph.main(["--data", self.tmp.name, "evaluate", "--run", self.run, "--product", pid]), 0)
            self.assertEqual(aph.main(["--data", self.tmp.name, "eval-report", "--out", str(out)]), 0)
        txt = out.read_text(encoding="utf-8")
        for k in ["Xếp hạng theo Opportunity Score", "Thứ tự ưu tiên xác minh", "16. **Hành động tiếp theo:**", "10. **Risk Gates:**",
                  "Hãng A · Cảm biến cửa Hãng A WiFi · WiFi", "Velocity: ≈"]:
            self.assertIn(k, txt)
        self.assertEqual(aph.ranking(self.st)[0]["status"], "GO")


class TestAgent07Completion(Agent07Base):
    """Giai đoạn 1 (2026-11-24 22:00): hồ sơ model, 2 loại xếp hạng, xu hướng gián tiếp, Presence/Velocity, Gate Tài chính, end-to-end."""

    # 1.4 Presence vs Velocity
    def test_single_snapshot_two_platforms_is_presence_only(self):
        pid = self.product()["product_id"]
        a = self.listing(pid, 280000, "S1", "lazada", sold=500)
        self.listing(pid, 285000, "S2", "shopee", sold=800); self.listing(pid, 290000, "S3", "shopee", sold=300)
        self.profile(pid, [a["id"]])
        d = self.ev(pid)
        g = d["groups"]["demand"]
        self.assertEqual(g["level"], "presence"); self.assertIsNone(g["velocity"])
        self.assertLessEqual(g["score"], 50)
        self.assertEqual(g["presence"]["platforms"], ["lazada", "shopee"])
        self.assertEqual(d["gates"]["demand"]["status"], "insufficient")
        self.assertIn("Velocity", d["gates"]["demand"]["reason"])

    def test_velocity_needs_same_listing_and_min_days(self):
        pid = self.product()["product_id"]
        self.listing(pid, 280000, "S1", sold=100, observed_at=self.day(3)); self.listing(pid, 280000, "S1", sold=160, observed_at=self.day(0))
        self.assertIsNone(self.ev(pid)["groups"]["demand"]["velocity"])  # < 7 ngày
        self.listing(pid, 280000, "S1", sold=40, observed_at=self.day(30))
        v = self.ev(pid)["groups"]["demand"]["velocity"]
        self.assertEqual(v["units_per_30d"], 120.0)  # (160-40) trong 30 ngày

    def test_velocity_pass_and_fail(self):
        self.assertEqual(self.ev(self.strong())["gates"]["demand"]["status"], "pass")
        e = self.ev(self.strong(demand="weak", tag="w"))
        self.assertEqual(e["gates"]["demand"]["status"], "fail"); self.assertEqual(e["status"], "NO_GO")

    # 1.3 Xu hướng
    def test_product_trend_is_indirect_variant_trend_direct(self):
        pid = self.strong()
        g = self.ev(pid)["groups"]["trend"]
        self.assertEqual((g["level"], g["basis"], g["scope"]), ("product", "indirect", "product"))
        self.assertIn("Bằng chứng gián tiếp", g["reason"])
        ids = [r["id"] for r in self.st.records(pid, "observation") if r["metric"] in aph.TREND_ATTENTION]
        self.st.append(self.run, "trend", {"product_id": pid, "variant_key": self.V, "stage": "stable", "basis": "từ khóa model (test)", "evidence": ids})
        self.st.append(self.run, "trend", {"product_id": pid, "variant_key": "model-khac", "stage": "declining", "basis": "test", "evidence": ids})
        v = self.ev(pid)["groups"]["trend"]
        self.assertEqual((v["level"], v["stage"], v["basis"]), ("variant", "stable", "observed"))
        self.assertGreater(v["confidence"], g["confidence"])

    # 1.5 Tài chính
    def test_financial_rejects_unverified_cost(self):
        pid = self.strong(real_cost=False)
        w = self.supplier(pid, basis="wholesale_listed", price=150000, verification="observed", evidence_excerpt="Giá sỉ 150.000đ")
        e = self.ev(pid)
        self.assertEqual(e["gates"]["financial"]["status"], "insufficient")
        self.assertIn("giá sỉ niêm yết", e["gates"]["financial"]["reason"].lower())
        # mô phỏng đủ chi phí nhưng giá vốn là giá sỉ niêm yết -> vẫn chưa đạt
        inp = full_inputs(unit_cost={"conservative": 150000, "base": 150000, "optimistic": 150000, "ref": w["id"]})
        self.st.append(self.run, "profit", {"product_id": pid, "inputs": inp, "variant": self.V, **aph.profit_sim(inp, BUSINESS)}, validate_kind=False)
        e = self.ev(pid)
        self.assertEqual(e["gates"]["financial"]["status"], "insufficient")
        self.assertEqual(e["groups"]["financial"]["basis"], "estimated")
        self.assertNotEqual(e["status"], "GO")

    def test_financial_rejects_estimated_shop_costs(self):
        pid = self.strong(real_cost=False)
        sup = self.supplier(pid)
        self.review([sup["id"]])
        p = pathlib.Path(self.tmp.name) / "assumptions" / "assumptions.json"
        p.write_text(json.dumps({"assumptions": [{"id": "cost_ads_pct", "value": 10, "verification": "estimated"}]}), encoding="utf-8")
        inp = full_inputs(unit_cost={"conservative": 140000, "base": 140000, "optimistic": 140000, "ref": sup["id"]},
                          ads_pct={"conservative": 10, "base": 10, "optimistic": 10, "ref": "assumption:cost_ads_pct"})
        self.st.append(self.run, "profit", {"product_id": pid, "inputs": inp, "variant": self.V, **aph.profit_sim(inp, BUSINESS)}, validate_kind=False)
        e = self.ev(pid)
        self.assertEqual(e["gates"]["financial"]["status"], "insufficient")
        self.assertIn("cost_ads_pct", e["gates"]["financial"]["reason"])

    # 1.1 Hồ sơ model
    def test_profile_validation_and_identity(self):
        pid = self.product()["product_id"]
        ls = [self.listing(pid, p, f"S{p}") for p in (280000, 285000, 290000)]
        base = {"product_id": pid, "variant_key": self.V, "model_name": "x", "evidence": [ls[0]["id"]], "evidence_excerpt": "Cảm biến Hãng A"}
        with self.assertRaises(aph.APHError):  # thương hiệu không có trong bằng chứng
            self.st.append(self.run, "variant_profile", {**base, "brand": "Hunonic", "protocol": "wifi"})
        with self.assertRaises(aph.APHError):  # giao thức không có trong bằng chứng
            self.st.append(self.run, "variant_profile", {**base, "brand": "Hãng A", "protocol": "zigbee"})
        with self.assertRaises(aph.APHError):  # nhiều giao thức phải là nhóm
            self.st.append(self.run, "variant_profile", {**base, "brand": "Hãng A", "protocol": "mixed"})
        self.assertEqual(self.ev(pid)["gates"]["identity"]["status"], "insufficient")  # chưa có hồ sơ
        self.st.append(self.run, "variant_profile", {**base, "brand": "Hãng A", "protocol": "wifi"})
        again = self.st.append(self.run, "variant_profile", {**base, "brand": "Hãng A", "protocol": "wifi"})
        self.assertEqual(again["status"], "existing")
        e = self.ev(pid)
        self.assertEqual(e["gates"]["identity"]["status"], "pass")
        self.assertTrue(e["model"]["display"].startswith("Hãng A · x · WiFi"))
        self.st.append(self.run, "variant_profile", {**base, "brand": "Hãng A", "protocol": "mixed", "is_group": True, "group_note": "nhiều hãng"})
        self.assertIn("nhóm nhiều mẫu", self.ev(pid)["gates"]["identity"]["reason"])

    def test_listing_with_other_protocol_flags_identity(self):
        pid = self.strong()
        bad = self.listing(pid, 295000, "Shop Z", evidence_excerpt="Cảm biến Hãng A Zigbee ₫295,000", title="Cảm biến Hãng A Zigbee")
        g = self.ev(pid)["gates"]["identity"]
        self.assertEqual(g["status"], "insufficient"); self.assertIn(bad["id"], g["reason"])

    # 1.2 Xếp hạng
    def test_two_rankings_are_distinct_and_score_sorted(self):
        hi = self.strong(tag="hi")
        lo = self.product(name="SP thiếu dữ liệu", fk="z|z")["product_id"]
        for p in (100000, 101000, 102000):
            self.listing(lo, p, f"T{p}", variant="m-lo")
        for pid, v in ((hi, self.V), (lo, "m-lo")):
            self.st.append(self.run, "evaluation", aph.evaluate(self.st, pid, v))
        by_score = aph.ranking(self.st, "score")
        totals = [r["total"] for r in by_score if r["total"] is not None]
        self.assertEqual(totals, sorted(totals, reverse=True))
        self.assertEqual(aph.ranking(self.st, "verification")[0]["status"], "GO")
        with self.assertRaises(aph.APHError):
            aph.ranking(self.st, "khac")

    # 1.6 chống ghi trùng + end-to-end qua CLI theo các bước Orchestrator
    def test_orchestrator_end_to_end_cli(self):
        import contextlib, io
        pid = self.strong(real_cost=False)
        D = ["--data", self.tmp.name]
        def cli(*args):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = aph.main([*D, *args])
            self.assertEqual(rc, 0, buf.getvalue())
            return json.loads(buf.getvalue())
        run = cli("new-run", "--type", "manual", "--request", "Đánh giá cơ hội cảm biến cửa", "--plan", '["opportunity_evaluator"]')["run_id"]
        ready = cli("readiness", "--product", pid)
        self.assertFalse(ready[0]["ready_for_go"])
        self.assertTrue(any(c["item"].startswith("Giá vốn") and not c["ok"] for c in ready[0]["checks"]))
        first = cli("evaluate", "--run", run, "--product", pid, "--variant", self.V)
        self.assertEqual((first["write"], first["status"]), ("new", "CONDITIONAL_GO"))
        self.assertEqual(cli("evaluate", "--run", run, "--product", pid, "--variant", self.V)["write"], "unchanged")
        sup = self.supplier(pid); self.review([sup["id"]]); self.profit(pid, sup["id"])
        second = cli("evaluate", "--run", run, "--product", pid, "--variant", self.V)
        self.assertEqual((second["write"], second["status"]), ("new", "GO"))
        diff = cli("eval-diff", "--product", pid, "--variant", self.V)
        self.assertEqual(diff["status"], {"from": "CONDITIONAL_GO", "to": "GO"})
        out = pathlib.Path(self.tmp.name) / "bao-cao.md"
        cli("eval-report", "--out", str(out), "--run", run)
        txt = out.read_text(encoding="utf-8")
        self.assertIn("Trạng thái: GO**, vì mọi Gate đạt", txt)
        cli("finish-run", "--run", run, "--summary", "e2e", "--agents", '["opportunity_evaluator"]')
        self.assertEqual(cli("reindex")["products"], 1)
        evs = self.st.records(pid, "evaluation")
        self.assertEqual(len(evs), 2)  # 1 bản không đổi không bị ghi trùng
        self.assertEqual(evs[0]["id"], first["id"])

    def test_variant_link_rerun_not_duplicated(self):
        pid = self.product()["product_id"]
        sup = self.supplier(pid, variant=None, name="Cảm biến cửa Wifi Hãng A")
        d = {"product_id": pid, "variant_key": self.V, "target": sup["id"], "reason": "Tên ghi WiFi Hãng A", "evidence_excerpt": "Wifi Hãng A"}
        a = self.st.append(self.run, "variant_link", d)
        b = self.st.append(self.run, "variant_link", d)
        self.assertEqual((b["id"], b["status"]), (a["id"], "existing"))
        self.assertEqual(len(self.st.records(kind="variant_link")), 1)

    def test_scoring_v1_still_works(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="price")
        ev = aph.score(self.st, pid, {"demand": {"score": 5, "why": "x"}})
        self.assertEqual(ev["weights_version"], "v1")
        self.assertIn("suggested_research_status", aph.gate(ev))


class DemandBase(Agent07Base):
    def dem(self, pid, theme, aspect, mentions, sample, platform="lazada", **kw):
        src = self.obs(pid, metric="complaint_mention", url=f"https://{platform}.example/review", value=mentions,
                       evidence_excerpt=f"{mentions} đánh giá: {theme}")
        d = {"product_id": pid, "type": "complaint", "theme": theme, "evidence": [src["id"]], "aspect": aspect,
             "mentions": mentions, "sample_size": sample, "platform": platform, "variant_key": self.V, **kw}
        return self.st.append(self.run, "demand", d)



class TestDemandAnalyst(DemandBase):
    """Agent 03 Demand Analyst: tổng hợp nhu cầu theo model, không suy diễn ngoài dữ liệu."""

    def test_no_data_stays_unknown(self):
        pid = self.product()["product_id"]
        d = aph.demand_analysis(self.st, pid, None)
        self.assertIsNone(d["demand_score"]["score"])
        self.assertIsNone(d["velocity"])
        self.assertTrue(all(q["answer"].startswith("UNKNOWN") for q in d["questions"]))
        self.assertEqual(d["seasonality"]["status"], "unknown")
        self.assertTrue(d["missing_data"])

    def test_presence_vs_velocity_and_platforms(self):
        pid = self.strong(real_cost=False)
        d = aph.demand_analysis(self.st, pid, self.V)
        self.assertEqual(d["demand_score"]["level"], "velocity")
        self.assertEqual(d["velocity"]["listings_measured"], 3)
        self.assertEqual(set(d["platform_differences"]), {"lazada", "shopee"})
        self.assertIsNotNone(d["platform_differences"]["shopee"]["units_per_30d"])
        self.assertIn("tiktok_shop", " ".join(d["missing_data"]))

    def test_review_themes_small_sample_flagged(self):
        pid = self.strong(real_cost=False)
        self.dem(pid, "Pin nhanh hết", "pain_point", 2, 12)
        self.dem(pid, "Kết nối WiFi rớt", "return_reason", 9, 60)
        self.dem(pid, "Chống trộm khi đi vắng", "buying_reason", 5, 60, type="purchase_intent")
        d = aph.demand_analysis(self.st, pid, self.V)
        pain = {t["theme"]: t for t in d["pain_points"]}
        self.assertIn("mẫu nhỏ", pain["Pin nhanh hết"]["reliability"])
        self.assertEqual(d["non_buy_reasons"][0]["share"], 0.15)
        self.assertIn("lặp lại", d["non_buy_reasons"][0]["reliability"])
        self.assertEqual(d["buying_motivations"][0]["theme"], "Chống trộm khi đi vắng")

    def test_other_model_demand_excluded(self):
        pid = self.strong(real_cost=False)
        self.dem(pid, "Lỗi của model khác", "pain_point", 5, 50, variant_key="model-khac-zigbee")
        d = aph.demand_analysis(self.st, pid, self.V)
        self.assertEqual(d["pain_points"], [])

    def test_seasonality_needs_a_year(self):
        pid = self.product()["product_id"]
        short = [{"t": self.day(7 * i), "v": 10 + i} for i in range(20)]
        self.obs(pid, metric="search_interest", url="https://trends.google.com/s", value=10, series=short)
        self.assertEqual(aph.demand_analysis(self.st, pid, None)["seasonality"]["status"], "unknown")
        year = [{"t": self.day(7 * i), "v": 60 if (self.TODAY - dt.timedelta(days=7 * i)).month == 6 else 20} for i in range(53)]
        self.obs(pid, metric="search_interest", url="https://trends.google.com/y", value=20, series=year)
        s = aph.demand_analysis(self.st, pid, None)["seasonality"]
        self.assertEqual((s["status"], s["peak_months"]), ("indicative", [6]))

    def test_engagement_is_not_sales(self):
        pid = self.product()["product_id"]
        self.obs(pid, metric="content_signal", url="https://www.tiktok.com/v", value=500000, platform="tiktok")
        d = aph.demand_analysis(self.st, pid, None)
        self.assertEqual(len(d["engagement"]["signals"]), 1)
        self.assertIsNone(d["velocity"])
        self.assertTrue(d["questions"][0]["answer"].startswith("UNKNOWN"))

    def test_invalid_aspect_rejected(self):
        pid = self.product()["product_id"]
        o = self.obs(pid, metric="complaint_mention")
        with self.assertRaises(aph.APHError):
            self.st.append(self.run, "demand", {"product_id": pid, "type": "complaint", "theme": "x", "evidence": [o["id"]], "aspect": "doan_mo"})

    def test_cli_rerun_not_duplicated_and_not_input_to_evaluator(self):
        import contextlib, io
        pid = self.strong(real_cost=False)
        def cli(*args):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = aph.main(["--data", self.tmp.name, *args])
            self.assertEqual(rc, 0, buf.getvalue())
            return json.loads(buf.getvalue())
        out = pathlib.Path(self.tmp.name) / "demand.md"
        a = cli("demand", "--run", self.run, "--product", pid, "--variant", self.V, "--out", str(out))
        b = cli("demand", "--run", self.run, "--product", pid, "--variant", self.V)
        self.assertEqual((a[0]["write"], b[0]["write"]), ("new", "unchanged"))
        self.assertEqual(len(self.st.records(pid, "demand_analysis")), 1)
        self.assertIn("Có nhu cầu thật không", out.read_text(encoding="utf-8"))
        snap = {x["kind"] for x in aph.evaluate(self.st, pid, self.V)["input_snapshot"]}
        self.assertNotIn("demand_analysis", snap)


class TestCompetitorAnalyst(DemandBase):
    """Agent 04 Competitor Analyst: đối thủ theo model, không khái quát từ mẫu nhỏ."""

    def test_no_listings_no_distribution(self):
        pid = self.product()["product_id"]
        d = aph.competitor_analysis(self.st, pid, None)
        # chưa thu thập: số tin/shop là UNKNOWN (None), không phải 0, và không suy ra lợi thế
        self.assertEqual((d["competitor_count"]["listings"], d["competitor_count"]["shops"], d["price_distribution"]), (None, None, {}))
        self.assertEqual(d["competitor_state"]["state"], "not_collected")
        self.assertEqual(d["advantages"], [])
        self.assertIsNone(d["competitor_score"]["score"])
        self.assertIsNone(d["brand_concentration"]["basis"])

    def test_distribution_per_platform_and_small_platform(self):
        pid = self.strong(real_cost=False)
        d = aph.competitor_analysis(self.st, pid, self.V)
        self.assertEqual(d["price_distribution"]["lazada"]["n"], 2)
        self.assertIn("note", d["price_distribution"]["lazada"])  # 2 tin < 3: không tính phân bố
        self.assertEqual(d["competitor_count"]["shops"], 3)
        self.assertIn("Tin đăng đúng model trên tiktok_shop", d["missing_data"])
        self.assertEqual(d["brand_concentration"]["shops_with_sales"], 3)

    def test_gap_strength_depends_on_sample(self):
        pid = self.strong(real_cost=False)
        self.dem(pid, "Pin nhanh hết", "pain_point", 1, 8)
        weak = aph.competitor_analysis(self.st, pid, self.V)
        self.assertEqual(weak["market_gaps"][0]["strength"], "giả thuyết (mẫu nhỏ)")
        self.dem(pid, "Pin nhanh hết", "pain_point", 4, 40, platform="shopee")
        strong = aph.competitor_analysis(self.st, pid, self.V)
        g = [x for x in strong["market_gaps"] if "Pin" in x["gap"]][0]
        self.assertEqual(g["strength"], "khoảng trống có cơ sở")

    def test_cli_dedup_and_not_evaluator_input(self):
        import contextlib, io
        pid = self.strong(real_cost=False)
        def cli(*args):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = aph.main(["--data", self.tmp.name, *args])
            self.assertEqual(rc, 0, buf.getvalue())
            return json.loads(buf.getvalue())
        out = pathlib.Path(self.tmp.name) / "comp.md"
        a = cli("competitors", "--run", self.run, "--product", pid, "--variant", self.V, "--out", str(out))
        b = cli("competitors", "--run", self.run, "--product", pid, "--variant", self.V)
        self.assertEqual((a[0]["write"], b[0]["write"]), ("new", "unchanged"))
        self.assertIn("Phân bố giá", out.read_text(encoding="utf-8"))
        self.assertNotIn("competitor_analysis", {x["kind"] for x in aph.evaluate(self.st, pid, self.V)["input_snapshot"]})



class TestPipelineIntegration(DemandBase):
    """Phase 4: Agent 03 → 04 → 07 chạy nối tiếp trên cùng dữ liệu, chống ghi trùng."""

    def cli(self, *args):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = aph.main(["--data", self.tmp.name, *args])
        self.assertEqual(rc, 0, buf.getvalue())
        return json.loads(buf.getvalue())

    def test_pipeline_end_to_end(self):
        go = self.strong(real_cost=True, tag="go")
        weak = self.strong(real_cost=False, demand="weak", tag="weak")
        out = pathlib.Path(self.tmp.name) / "tong-hop.md"
        first = self.cli("pipeline", "--run", self.run, "--all", "--out", str(out))
        by = {r["product_id"]: r for r in first}
        self.assertEqual(by[go]["status"], "GO")
        self.assertTrue(by[go]["ready_for_go"])
        self.assertNotEqual(by[weak]["status"], "GO")
        self.assertTrue(all(set(r["writes"].values()) == {"new"} for r in first))
        again = self.cli("pipeline", "--run", self.run, "--all")
        self.assertTrue(all(set(r["writes"].values()) == {"unchanged"} for r in again))
        txt = out.read_text(encoding="utf-8")
        self.assertIn("Việc tiếp theo theo người/Agent phụ trách", txt)
        self.assertLess(txt.index("| GO |"), txt.index("NEED MORE DATA") if "NEED MORE DATA" in txt else len(txt))
        # Dữ liệu mới của Agent 03 làm thay đổi phân tích nhu cầu và đánh giá, không chạm bản cũ
        self.dem(go, "Pin nhanh hết", "pain_point", 4, 40)
        third = {r["product_id"]: r for r in self.cli("pipeline", "--run", self.run, "--product", go, "--variant", self.V)}
        self.assertEqual(third[go]["writes"]["demand_analysis"], "new")
        self.assertEqual(third[go]["writes"]["evaluation"], "new")
        self.assertEqual(len(self.st.records(go, "demand_analysis")), 2)
        self.assertEqual(len(self.st.records(go, "evaluation")), 2)


class TestPhase5Integrity(DemandBase):
    """Giai đoạn 5: nhãn bán chính xác/làm tròn, cùng một tin đăng, tin nhiều phân loại, trạng thái đối thủ, NCC theo pháp nhân."""

    def test_sold_label_precision(self):
        cases = {"552 sold": (552, "exact"), "16 Đã bán": (16, "exact"), "2k+ sold": (2000, "rounded"), "1,2k": (1200, "rounded"),
                 "2.600+": (2600, "rounded"), "Đã bán 349": (349, "exact"), None: (None, "unknown")}
        for label, (v, p) in cases.items():
            info = aph.sold_label_info(label)
            self.assertEqual((info["value"], info["precision"]), (v, p), label)

    def test_listing_key_same_listing_across_url_forms(self):
        a = aph.listing_key({"url": "https://shopee.vn/product/1112786088/28767858796", "platform": "shopee"})
        b = aph.listing_key({"url": "https://shopee.vn/Cong-Tac-i.1112786088.28767858796?sp_atk=x", "platform": "shopee"})
        c = aph.listing_key({"url": "https://shopee.vn/product/1112786088/28767858797", "platform": "shopee"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(aph.listing_key({"url": "https://www.lazada.vn/products/x-i2589082813-s1.html?q=1"}), "lazada:2589082813")

    def test_velocity_same_listing_other_url_form(self):
        pid = self.product()["product_id"]
        for shop, item in (("1", "11"), ("2", "22"), ("3", "33")):
            self.listing(pid, 300000, f"s{shop}", "shopee", url=f"https://shopee.vn/product/{shop}/{item}", sold=100, observed_at=self.day(14))
            self.listing(pid, 300000, f"s{shop}", "shopee", url=f"https://shopee.vn/Ten-i.{shop}.{item}", sold=130, observed_at=self.day(0))
        g = self.ev(pid)["groups"]["demand"]
        self.assertEqual(g["level"], "velocity")
        self.assertEqual(g["velocity"]["listings_measured"], 3)
        self.assertEqual(g["presence"]["listings"], 3)  # 6 bản ghi nhưng chỉ 3 tin đăng

    def test_rounded_labels_never_give_velocity(self):
        pid = self.product()["product_id"]
        for i in range(3):
            self.listing(pid, 300000, f"s{i}", "shopee", url=f"https://shopee.vn/product/{i}/9", sold_label="1k+ sold", observed_at=self.day(14))
            self.listing(pid, 300000, f"s{i}", "shopee", url=f"https://shopee.vn/product/{i}/9", sold_label="2k+ sold", observed_at=self.day(0))
        e = self.ev(pid)
        self.assertEqual(e["groups"]["demand"]["level"], "presence")
        self.assertTrue(any("làm tròn" in r for r in e["risks"]))
        self.assertEqual(e["groups"]["demand"]["presence"]["sold_labels_rounded"], 3)

    def test_different_listings_never_compared(self):
        pid = self.product()["product_id"]
        self.listing(pid, 300000, "a", "shopee", url="https://shopee.vn/product/1/1", sold=10, observed_at=self.day(14))
        self.listing(pid, 300000, "b", "shopee", url="https://shopee.vn/product/1/2", sold=500, observed_at=self.day(0))
        self.listing(pid, 300000, "c", "lazada", url="https://www.lazada.vn/products/pdp-i1.html", sold=900, observed_at=self.day(0))
        self.assertEqual(self.ev(pid)["groups"]["demand"]["level"], "presence")

    def test_multi_sku_listing_excluded_from_price_and_sold(self):
        pid = self.product()["product_id"]
        for i, p in enumerate((300000, 310000, 320000)):
            self.listing(pid, p, f"s{i}", "shopee", url=f"https://shopee.vn/product/{i}/1", sold=10)
        self.listing(pid, 99000, "multi", "shopee", url="https://shopee.vn/product/9/9", sold_label="3k+ sold",
                     title="Cảm biến Hãng A WiFi 1, 2, 3 nút")  # heuristic tiêu đề
        self.listing(pid, 98000, "multi2", "shopee", url="https://shopee.vn/product/8/8", sold=900, sku_scope="multi_sku")
        e = self.ev(pid)
        self.assertEqual(e["groups"]["competition"]["price"]["median"], 310000)
        self.assertEqual(e["groups"]["demand"]["presence"]["cumulative_sold_label_total"], 30)
        self.assertEqual(e["groups"]["demand"]["presence"]["listings_multi_sku"], 2)
        ps = aph.price_stats(self.st, pid, variant=self.V)
        self.assertEqual((ps["n"], ps["median"]), (3, 310000))
        d = aph.competitor_analysis(self.st, pid, self.V)
        self.assertEqual(d["competitor_count"]["shops"], 5)  # vẫn là shop đối thủ
        self.assertEqual(d["price_distribution"]["shopee"]["median"], 310000)
        self.assertEqual(d["price_distribution"]["shopee"]["excluded_multi_sku"], 2)

    def test_competitor_states(self):
        pid = self.product()["product_id"]
        self.assertEqual(aph.competitor_analysis(self.st, pid, self.V)["competitor_state"]["state"], "not_collected")
        self.obs(pid, metric="search_result_count", url="https://shopee.vn/search?keyword=x", value=0, variant_key=self.V,
                 evidence_excerpt="0 kết quả")
        d = aph.competitor_analysis(self.st, pid, self.V)
        self.assertEqual((d["competitor_state"]["state"], d["competitor_count"]["shops"], d["advantages"]), ("searched_none_found", 0, []))
        self.listing(pid, 300000, "s1", "shopee", url="https://shopee.vn/product/1/1")
        d = aph.competitor_analysis(self.st, pid, self.V)
        self.assertEqual((d["competitor_state"]["state"], d["advantages"]), ("insufficient", []))

    def test_vendor_review_applies_by_domain(self):
        pid = self.product()["product_id"]
        old = self.supplier(pid, basis="retail_listed", price=300000, quote_status="need_quote", variant="khac")
        self.review([old["id"]])
        new = self.supplier(pid, basis="retail_listed", price=310000, quote_status="need_quote")  # cùng hang-a.example, đúng model
        g = self.ev(pid)["groups"]["supplier"]
        self.assertIsNotNone(g["score"])
        self.assertIn(new["id"], g["evidence_ids"])

    def test_remeasure_plan_no_records_and_due_dates(self):
        pid = self.product()["product_id"]
        self.listing(pid, 300000, "s1", "shopee", url="https://shopee.vn/product/1/1", sold=10, observed_at="2026-11-25")
        self.listing(pid, 300000, "s2", "shopee", url="https://shopee.vn/product/2/2", sold_label="2k+ sold", observed_at="2026-11-25")
        before = len(self.st.records())
        plan = aph.remeasure_plan(self.st, dt.date(2026, 11, 25))
        self.assertEqual(len(self.st.records()), before)  # chỉ đọc
        self.assertEqual({r["due"] for r in plan["rows"]}, {"2026-12-02"})
        self.assertEqual(plan["due"], 0)
        rounded = [r for r in plan["rows"] if r["listing"] == "shopee:2.2"][0]
        self.assertTrue(any("làm tròn" in w for w in rounded["warnings"]))
        self.assertEqual(aph.remeasure_plan(self.st, dt.date(2026, 12, 2))["due"], 2)
