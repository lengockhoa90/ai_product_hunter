"""Kiểm thử tools/aph.py. Chạy: python3 -m unittest discover -s tests -v

Dữ liệu trong test chỉ để kiểm tra logic công cụ, ghi vào thư mục tạm, không bao giờ ghi vào kho thật.
"""
import datetime as dt
import json
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

    def product(self, name="Cảm biến rò rỉ nước Zigbee", fk="water-leak-sensor|zigbee", **kw):
        d = {"canonical_name": name, "category": "smart_home", "function_key": fk, **kw}
        return self.st.add_product(self.run, d)

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
                                             "quote_status": "listed", "verification": "observed"})
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
