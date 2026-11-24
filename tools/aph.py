#!/usr/bin/env python3
"""AI Product Hunter - công cụ dữ liệu và tính toán (chỉ dùng thư viện chuẩn Python).

Mọi Agent ghi và đọc dữ liệu qua công cụ này. Claude không tự cộng điểm hay tính lợi nhuận bằng tay.

Bố cục dữ liệu (APH_DATA, mặc định /mnt/project-files/ai-product-hunter/data):
  products/<product_id>.json          hồ sơ sản phẩm (ghi có khóa)
  records/YYYY/MM/<run_id>.jsonl      mọi bản ghi của một lần chạy (append-only)
  runs/<run_id>.json                  nhật ký lần chạy
  assumptions/assumptions.json        giả định chi phí có nguồn
  index.sqlite                        chỉ mục dựng lại được (aph.py reindex)

Chạy `python3 tools/aph.py -h` để xem các lệnh.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA = "/mnt/project-files/ai-product-hunter/data"
CONFIG = REPO / "config"
SCHEMA = json.loads((REPO / "schemas" / "entities.json").read_text(encoding="utf-8"))

PROFIT_FIELDS = [
    "sell_price", "unit_cost", "inbound_shipping", "platform_fee_pct", "payment_fee_pct",
    "ads_pct", "seller_shipping", "packaging", "ops_per_unit", "return_rate_pct",
    "return_loss_pct", "other_per_unit", "fixed_test_cost", "moq",
]
SCENARIOS = ["conservative", "base", "optimistic"]


class APHError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            t = dt.datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def cfg(name: str):
    return load_json(CONFIG / f"{name}.json")


def strip_accents(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_text(s: str) -> str:
    s = strip_accents(s.lower())
    return re.sub(r"[^0-9a-z一-鿿]+", " ", s).strip()


def norm_key(s: str) -> str:
    parts = [re.sub(r"\s+", "-", norm_text(p)) for p in s.split("|")]
    return "|".join(p for p in parts if p)


def similarity(a: str, b: str) -> float:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return round(max(seq, jac), 3)


def value_in_excerpt(value, excerpt: str) -> bool:
    """Giá trị quan sát phải đọc được trong đoạn trích. Số: so chuỗi chữ số (bỏ dấu . , khoảng trắng)."""
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        digits = re.sub(r"\D", "", excerpt)
        cands = {str(int(value))} if float(value).is_integer() else {str(value).replace(".", ""), str(round(value, 1)).replace(".", "")}
        return any(c in digits for c in cands)
    if isinstance(value, str):
        return norm_text(value) in norm_text(excerpt)
    return True  # list/dict (ví dụ series): kiểm tra ở mức bản ghi con


def domain(url: str | None) -> str | None:
    if not url:
        return None
    d = urlparse(url).netloc.lower()
    return d[4:] if d.startswith("www.") else (d or None)


def check_type(val, t: str) -> bool:
    return {
        "str": isinstance(val, str),
        "num": isinstance(val, (int, float)) and not isinstance(val, bool),
        "int": isinstance(val, int) and not isinstance(val, bool),
        "bool": isinstance(val, bool),
        "list": isinstance(val, list),
        "dict": isinstance(val, dict),
        "any": True,
    }[t]


def validate(spec: dict, data: dict, label: str) -> None:
    errs = []
    for f, t in spec.get("required", {}).items():
        if f not in data or data[f] in (None, ""):
            errs.append(f"thiếu trường bắt buộc '{f}'")
        elif not check_type(data[f], t):
            errs.append(f"'{f}' phải là {t}")
    for f, t in spec.get("optional", {}).items():
        if f in data and data[f] is not None and not check_type(data[f], t):
            errs.append(f"'{f}' phải là {t}")
    for f, enum in spec.get("enum_fields", {}).items():
        if f in data and data[f] is not None and data[f] not in SCHEMA["enums"][enum]:
            errs.append(f"'{f}'='{data[f]}' không hợp lệ, cho phép: {SCHEMA['enums'][enum]}")
    known = set(spec.get("required", {})) | set(spec.get("optional", {}))
    extra = [k for k in data if k not in known]
    if extra:
        errs.append(f"trường không có trong schema: {extra}")
    if errs:
        raise APHError(f"{label}: " + "; ".join(errs))


# ---------------------------------------------------------------- store

class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    # paths
    @property
    def products_dir(self):
        return self.root / "products"

    def run_path(self, run_id):
        return self.root / "runs" / f"{run_id}.json"

    def records_path(self, run_id):
        # run_YYYYMMDDTHHMMSS_xxxx
        y, m = run_id[4:8], run_id[8:10]
        return self.root / "records" / y / m / f"{run_id}.jsonl"

    def init(self):
        for d in ["products", "records", "runs", "assumptions"]:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        ap = self.root / "assumptions" / "assumptions.json"
        if not ap.exists():
            ap.write_text(json.dumps({"assumptions": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    # lock for product files
    def _lock(self):
        lock = self.products_dir / ".lock"
        for _ in range(30):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, now_iso().encode())
                os.close(fd)
                return lock
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 120:
                        lock.unlink()
                        continue
                except FileNotFoundError:
                    continue
                time.sleep(1)
        raise APHError("Không lấy được khóa products/.lock sau 30 giây")

    # runs
    def new_run(self, run_type, request, depth, plan=None):
        if run_type not in SCHEMA["enums"]["run_type"]:
            raise APHError(f"run_type không hợp lệ: {run_type}")
        if depth not in SCHEMA["enums"]["depth"]:
            raise APHError(f"depth không hợp lệ: {depth}")
        t = dt.datetime.now(dt.timezone.utc)
        run_id = f"run_{t:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:4]}"
        data = {"run_id": run_id, "type": run_type, "request": request, "depth": depth,
                "plan": plan or [], "started_at": now_iso(), "finished_at": None,
                "agents_run": [], "summary": None}
        p = self.run_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def get_run(self, run_id):
        r = load_json(self.run_path(run_id))
        if r is None:
            raise APHError(f"Không có run {run_id}. Tạo bằng new-run trước.")
        return r

    def finish_run(self, run_id, summary, agents):
        r = self.get_run(run_id)
        r["finished_at"] = now_iso()
        r["summary"] = summary
        r["agents_run"] = agents
        self.run_path(run_id).write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        return r

    # products
    def products(self):
        if not self.products_dir.exists():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.products_dir.glob("prd_*.json"))]

    def product(self, pid):
        p = load_json(self.products_dir / f"{pid}.json")
        if p is None:
            raise APHError(f"Không có sản phẩm {pid}")
        return p

    def match(self, name, function_key=None, aliases=()):
        fk = norm_key(function_key) if function_key else None
        names = [name, *aliases]
        out = []
        for p in self.products():
            reason, score = None, 0.0
            if fk and p["function_key"] == fk:
                reason, score = "function_key", 1.0
            else:
                pnames = [p["canonical_name"], *p.get("aliases", [])]
                for a in names:
                    for b in pnames:
                        if norm_text(a) == norm_text(b):
                            reason, score = "alias_exact", 1.0
                        else:
                            s = similarity(a, b)
                            if s > score and reason is None:
                                score = s
            if reason or score >= 0.6:
                out.append({"product_id": p["product_id"], "canonical_name": p["canonical_name"],
                            "function_key": p["function_key"], "score": score,
                            "match": "exact" if reason else ("possible" if score >= 0.8 else "weak"),
                            "reason": reason or "name_similarity"})
        return sorted(out, key=lambda x: -x["score"])

    def add_product(self, run_id, data, force_new=False):
        self.get_run(run_id)
        data = dict(data)
        validate(SCHEMA["product"], data, "product")
        data["function_key"] = norm_key(data["function_key"])
        if data["category"] not in cfg("categories"):
            raise APHError(f"category '{data['category']}' không có trong config/categories.json")
        cands = self.match(data["canonical_name"], data["function_key"], data.get("aliases", []))
        exact = [c for c in cands if c["match"] == "exact"]
        if exact:
            pid = exact[0]["product_id"]
            lock = self._lock()
            try:
                p = self.product(pid)
                known = {norm_text(x) for x in [p["canonical_name"], *p.get("aliases", [])]}
                new_aliases = [a for a in [data["canonical_name"], *data.get("aliases", [])] if norm_text(a) not in known]
                if new_aliases:
                    p.setdefault("aliases", []).extend(new_aliases)
                    p["updated_at"] = now_iso()
                    self._write_product(p)
            finally:
                lock.unlink(missing_ok=True)
            return {"status": "existing", "product_id": pid, "added_aliases": new_aliases, "matched_by": exact[0]["reason"]}
        possible = [c for c in cands if c["match"] == "possible"]
        if possible and not force_new:
            return {"status": "needs_review", "candidates": possible,
                    "hint": "Kiểm tra có phải cùng sản phẩm không. Nếu cùng: dùng product_id có sẵn. Nếu khác: chạy lại với --force-new."}
        lock = self._lock()
        try:
            pid = f"prd_{uuid.uuid4().hex[:10]}"
            rec = {"product_id": pid, **data, "aliases": data.get("aliases", []), "relations": data.get("relations", []),
                   "first_seen_at": now_iso(), "created_by_run": run_id, "updated_at": now_iso()}
            self._write_product(rec)
        finally:
            lock.unlink(missing_ok=True)
        if possible:
            self.append(run_id, "note", {"product_id": pid, "text": f"Tạo mới dù có ứng viên gần giống: {[c['product_id'] for c in possible]}"}, validate_kind=False)
        return {"status": "created", "product_id": pid}

    def _write_product(self, p):
        self.products_dir.mkdir(parents=True, exist_ok=True)
        path = self.products_dir / f"{p['product_id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def relate(self, pid, rel, other):
        if rel not in ("complements", "variant_of", "substitute"):
            raise APHError("rel phải là complements | variant_of | substitute")
        self.product(other)
        lock = self._lock()
        try:
            p = self.product(pid)
            entry = {"type": rel, "product_id": other}
            if entry not in p.setdefault("relations", []):
                p["relations"].append(entry)
                p["updated_at"] = now_iso()
                self._write_product(p)
        finally:
            lock.unlink(missing_ok=True)
        return p["relations"]

    # records
    def append(self, run_id, kind, data, validate_kind=True):
        self.get_run(run_id)
        data = dict(data)
        if validate_kind:
            if kind not in SCHEMA["kinds"]:
                raise APHError(f"kind '{kind}' không hợp lệ, cho phép: {list(SCHEMA['kinds'])}")
            validate(SCHEMA["kinds"][kind], data, kind)
        if "product_id" in data:
            self.product(data["product_id"])
        if kind in ("observation", "demand") and not (data.get("product_id") or data.get("problem_ref")):
            raise APHError(f"{kind} cần product_id hoặc problem_ref")
        if data.get("problem_ref"):
            self.problem(data["problem_ref"])
        if kind == "problem":
            data["problem_key"] = norm_key(data["problem_key"])
            if data["category"] not in cfg("categories"):
                raise APHError(f"category '{data['category']}' không có trong config/categories.json")
            for p in self.records(kind="problem"):
                if p["problem_key"] == data["problem_key"] or similarity(p["statement"], data["statement"]) >= 0.85:
                    return {"status": "existing", "id": p["id"], "statement": p["statement"],
                            "hint": "Vấn đề này đã có. Gắn bằng chứng mới bằng problem_ref thay vì tạo lại."}
        self._source_rules(kind, data)
        if data.get("evidence"):
            known = {r["id"] for r in self.records()}
            missing = [e for e in data["evidence"] if e not in known]
            if missing:
                raise APHError(f"evidence tham chiếu id không có trong kho: {missing}. Ghi observation/listing trước rồi dùng id trả về")
        if kind == "vendor_review" and data.get("supplier_refs"):
            sups = {r["id"] for r in self.records(kind="supplier")}
            bad = [s for s in data["supplier_refs"] if s not in sups]
            if bad:
                raise APHError(f"supplier_refs không phải bản ghi supplier trong kho: {bad}")
        rec = {"id": f"{kind[:3]}_{uuid.uuid4().hex[:12]}", "kind": kind, "run_id": run_id,
               "collected_at": now_iso(), **data}
        path = self.records_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def problem(self, ref):
        for p in self.records(kind="problem"):
            if p["id"] == ref:
                return p
        raise APHError(f"Không có vấn đề (problem) {ref}")

    def problems_for(self, pid, recs=None):
        recs = recs if recs is not None else self.records()
        return {r["problem_ref"] for r in recs if r["kind"] == "solution" and r["product_id"] == pid}

    @staticmethod
    def _source_rules(kind, d):
        if kind == "observation":
            if not 0 <= d["confidence"] <= 1:
                raise APHError("confidence phải trong khoảng 0–1")
            v = d["verification"]
            if v in ("observed", "reported") and not d.get("source_url"):
                raise APHError(f"verification={v} bắt buộc có source_url (không được tạo nguồn)")
            if v in ("estimated", "inferred") and not d.get("note"):
                raise APHError(f"verification={v} bắt buộc có note giải thích cách ước tính/suy luận")
            if v == "estimated" and ("range_low" not in d or "range_high" not in d):
                raise APHError("verification=estimated bắt buộc có range_low và range_high")
            if v == "observed":
                ex = d.get("evidence_excerpt")
                if not ex or len(ex.strip()) < 8:
                    raise APHError("verification=observed bắt buộc có evidence_excerpt: đoạn trích nguyên văn từ trang chứa giá trị")
                if not value_in_excerpt(d["value"], ex):
                    raise APHError(f"Giá trị {d['value']!r} không xuất hiện trong evidence_excerpt. Trích lại đúng đoạn chứa con số, hoặc ghi chú cách quy đổi vào note và dùng verification khác")
        if kind == "listing" and d.get("price_vnd") is not None and d["verification"] == "observed":
            ex = d.get("evidence_excerpt") or ""
            if len(ex.strip()) < 8 or not value_in_excerpt(d["price_vnd"], ex):
                raise APHError("listing có price_vnd (observed) phải có evidence_excerpt chép nguyên văn chứa đúng giá")
        if kind == "supplier":
            tiers = d.get("price_tiers") or []
            if tiers and not d.get("price_basis"):
                raise APHError("supplier có price_tiers phải ghi price_basis (retail_listed | wholesale_listed | dealer_quote | source_reference): giá bán lẻ không phải giá nhập")
            if d["verification"] == "observed":
                ex = d.get("evidence_excerpt")
                if not ex or len(ex.strip()) < 8:
                    raise APHError("supplier verification=observed bắt buộc có evidence_excerpt chép nguyên văn từ trang")
                for t in tiers:
                    if not isinstance(t, dict) or "price" not in t:
                        raise APHError("mỗi price_tiers phải là {\"price\": số, \"min_qty\": số, ...}")
                    if not value_in_excerpt(t["price"], ex):
                        raise APHError(f"Giá {t['price']!r} trong price_tiers không xuất hiện trong evidence_excerpt")
            if d["quote_status"] in ("listed", "quoted") and not tiers:
                raise APHError("quote_status listed/quoted cần price_tiers; chưa có giá thì ghi need_quote")
        if kind == "vendor_review":
            crit = d["criteria"]
            miss = [c for c in VENDOR_CRITERIA if c not in crit]
            extra = [c for c in crit if c not in VENDOR_CRITERIA]
            if miss or extra:
                raise APHError(f"criteria phải có đúng 6 tiêu chí {VENDOR_CRITERIA}; thiếu {miss}, thừa {extra}")
            allowed = SCHEMA["enums"]["vendor_criterion_status"]
            for c, v in crit.items():
                if not isinstance(v, dict) or v.get("status") not in allowed:
                    raise APHError(f"criteria.{c} cần status thuộc {allowed}")
                if v["status"] in ("verified", "stated"):
                    if not v.get("source_url") or len((v.get("evidence_excerpt") or "").strip()) < 8:
                        raise APHError(f"criteria.{c} status={v['status']} cần source_url và evidence_excerpt chép nguyên văn")
                    own = {domain("https://" + x) for x in [d["vendor_key"], *d.get("vendor_domains", [])]}
                    if v["status"] == "verified" and domain(v["source_url"]) in own:
                        raise APHError(f"criteria.{c}: verified phải đối chiếu từ nguồn độc lập, không phải trang của chính NCC (dùng stated)")
                if v["status"] in ("user_input", "missing") and not (v.get("value") or v.get("note")):
                    raise APHError(f"criteria.{c} status={v['status']} cần value hoặc note")
        if kind == "solution" and d["fit"] == "bundle_component" and not d.get("bundle_with"):
            raise APHError("fit=bundle_component cần bundle_with (danh sách product_id cùng bộ)")
        if kind in ("demand", "trend") and not d["evidence"]:
            raise APHError(f"{kind} phải có evidence (danh sách id observation/listing)")
        if kind == "problem" and not d["evidence"] and d.get("status", "candidate") != "candidate":
            raise APHError("problem chưa có evidence chỉ được ở trạng thái candidate; gắn bằng chứng qua problem_ref rồi mới chuyển validated")

    def records(self, product_id=None, kind=None, include_retracted=False):
        """Đọc bản ghi. Bản ghi bị rút lại (kind=retraction, target=<id>) bị bỏ qua, trừ khi include_retracted."""
        out = []
        base = self.root / "records"
        if not base.exists():
            return out
        allr = []
        for path in sorted(base.rglob("*.jsonl")):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        allr.append(json.loads(line))
        retracted = set() if include_retracted else {r["target"] for r in allr if r["kind"] == "retraction"}
        for r in allr:
            if r["id"] in retracted:
                continue
            if product_id and r.get("product_id") != product_id:
                continue
            if kind and r["kind"] != kind:
                continue
            out.append(r)
        return sorted(out, key=lambda r: r["collected_at"])

    def retract(self, run_id, target, reason):
        """Rút lại một bản ghi sai (kho chỉ ghi thêm, không xóa). Bản ghi gốc vẫn còn trong file để kiểm tra lại."""
        if not reason or len(reason.strip()) < 8:
            raise APHError("retract cần --reason giải thích vì sao bản ghi sai")
        hit = [r for r in self.records(include_retracted=True) if r["id"] == target]
        if not hit:
            raise APHError(f"Không có bản ghi {target}")
        if hit[0]["kind"] == "retraction":
            raise APHError("Không rút lại một bản ghi retraction")
        return self.append(run_id, "retraction", {"target": target, "target_kind": hit[0]["kind"], "reason": reason})

    def assumptions(self):
        return load_json(self.root / "assumptions" / "assumptions.json", {"assumptions": []})["assumptions"]

    # status
    def status(self, pid, decisions=None):
        """Trạng thái hiện tại = quyết định mới nhất. Truyền sẵn `decisions` để tránh quét lại toàn bộ kho."""
        st = {"research_status": "new", "business_status": "discovered"}
        for r in (decisions if decisions is not None else self.records(pid, "decision")):
            for k in st:
                if r.get(k):
                    st[k] = r[k]
        return st


# ---------------------------------------------------------------- confidence

def freshness(rec, metric_kind, cfgc, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    t = parse_time(rec.get("observed_at")) or parse_time(rec.get("collected_at"))
    if not t:
        return 0.5
    age = max(0.0, (now - t).total_seconds() / 86400)
    hl = cfgc["half_life_days"].get(metric_kind, cfgc["half_life_days"]["default"])
    return 0.5 ** (age / hl)


def evidence_quality(rec, cfgc, now=None):
    vf = cfgc["verification_factor"]
    kind = rec["kind"]
    if kind == "observation":
        q = vf[rec["verification"]] * rec["confidence"]
        return q * freshness(rec, rec["metric"], cfgc, now)
    if kind in ("listing", "supplier"):
        return vf[rec["verification"]] * 0.8 * freshness(rec, "supplier" if kind == "supplier" else "price", cfgc, now)
    if kind == "demand":
        n = rec.get("independent_sources") or len(rec["evidence"])
        return min(1.0, 0.4 + 0.2 * n) * freshness(rec, "demand_signal", cfgc, now)
    if kind == "profit":
        return rec.get("input_confidence", 0.0) if rec.get("status") == "complete" else 0.0
    return 0.0


def compute_confidence(store: Store, pid, now=None):
    cfgc, cfgw = cfg("confidence"), cfg("scoring_weights")
    allrecs = store.records()
    probs = store.problems_for(pid, allrecs)
    recs = [r for r in allrecs if r.get("product_id") == pid or (r.get("problem_ref") in probs and r["kind"] != "solution")]
    by_id = {r["id"]: r for r in allrecs}
    result, total_w, acc = {}, 0, 0.0
    for crit, spec in cfgc["criterion_evidence"].items():
        items = [r for r in recs if (r["kind"] == "observation" and r["metric"] in spec["metrics"]) or r["kind"] in spec["kinds"]]
        if crit == "profit":
            items = [r for r in items if r["kind"] != "profit"] + ([r for r in recs if r["kind"] == "profit"][-1:])
            # profit criterion needs a complete profit simulation
            if not any(r["kind"] == "profit" and r.get("status") == "complete" for r in items):
                items = []
        qs = sorted((evidence_quality(r, cfgc, now) for r in items), reverse=True)
        sources = set()
        for r in items:
            d = domain(r.get("source_url") or r.get("url"))
            if d:
                sources.add(d)
            if r["kind"] == "demand":
                for e in r["evidence"]:
                    er = by_id.get(e)
                    if er and domain(er.get("source_url") or er.get("url")):
                        sources.add(domain(er.get("source_url") or er.get("url")))
            if r["kind"] == "profit":
                sources.add("profit_simulation")
        conf = (sum(qs[:3]) / len(qs[:3])) if qs else 0.0
        conf *= min(1.0, len(sources) / spec["min_sources"]) if spec["min_sources"] else 1.0
        attention_only = None
        if spec.get("attention_metrics") is not None:
            grade = [r for r in items if (r["kind"] == "observation" and r["metric"] not in spec["attention_metrics"])
                     or (r["kind"] == "demand" and r["type"] in spec["demand_grade_types"])]
            attention_only = bool(items) and not grade
            if attention_only:
                conf = min(conf, spec["attention_only_cap"])
        tps = None
        if spec.get("min_timepoints"):
            tps = timepoints(items)
            conf *= min(1.0, tps / spec["min_timepoints"])
        w = cfgw["criteria"][crit]["weight"]
        total_w += w
        acc += w * conf
        result[crit] = {"confidence": round(conf, 3), "evidence": len(items), "sources": len(sources),
                        **({"timepoints": tps} if tps is not None else {}),
                        **({"attention_only": attention_only} if attention_only is not None else {})}
    vc = cfgc.get("verification_class", {})
    classes = {}
    for r in recs:
        if r.get("verification"):
            c = vc.get(r["verification"], r["verification"])
            classes[c] = classes.get(c, 0) + 1
    return {"data_confidence": round(100 * acc / total_w, 1), "criteria": result,
            "evidence_by_class": classes, "linked_problems": sorted(probs)}


def timepoints(items):
    pts = set()
    for r in items:
        if r["kind"] != "observation":
            continue
        if r.get("series"):
            for p in r["series"]:
                if isinstance(p, dict) and p.get("t"):
                    pts.add(str(p["t"])[:10])
        else:
            t = r.get("observed_at") or r.get("collected_at")
            if t:
                pts.add(t[:10])
    return len(pts)


# ---------------------------------------------------------------- profit

def _val(field, spec, scenario):
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, dict) and scenario in spec and spec[scenario] is not None:
        return float(spec[scenario])
    raise KeyError(field)


FEE_FIELDS = ("platform_fee_pct", "payment_fee_pct")


def check_fee_refs(inputs: dict, assumptions: dict, platform: str, category: str | None, shop_type, today=None):
    """Phí sàn chỉ được lấy từ assumption có nguồn, có ngày, đúng sàn, đúng ngành hàng, đúng loại shop, còn hạn.
    Trả về danh sách lỗi; rỗng = hợp lệ."""
    today = today or dt.date.today().isoformat()
    errs = []
    for f in FEE_FIELDS:
        spec = inputs.get(f)
        ref = spec.get("ref") if isinstance(spec, dict) else None
        ids = ref.split(":", 1)[1].split("+") if ref and ref.startswith("assumption:") else None
        if not ids:
            errs.append(f"{f}: phải tham chiếu assumption:<id> (biểu phí có nguồn), không dùng số tự nhập")
            continue
        for aid in ids:
            a = assumptions.get(aid)
            if a is None:
                errs.append(f"{f}: không có assumption '{aid}'"); continue
            if a.get("status") == "missing" or a.get("value") is None:
                errs.append(f"{f}: '{aid}' đang missing"); continue
            if not a.get("source_url") or not a.get("source_date"):
                errs.append(f"{f}: '{aid}' thiếu nguồn hoặc ngày cập nhật"); continue
            if a.get("platform") != platform:
                errs.append(f"{f}: '{aid}' là phí của '{a.get('platform')}', không phải '{platform}'")
            cats = a.get("applies_to_categories") or []
            if "all" not in cats and category not in cats:
                errs.append(f"{f}: '{aid}' không áp dụng cho ngành hàng '{category}' (chỉ: {cats or 'chưa ghi'})")
            st = a.get("shop_type", "all")
            if st != "all" and st != shop_type:
                errs.append(f"{f}: '{aid}' áp dụng cho shop '{st}', loại shop hiện tại: {shop_type or 'chưa biết'}")
            if a.get("valid_until") and a["valid_until"] < today:
                errs.append(f"{f}: '{aid}' đã quá hạn kiểm tra lại ({a['valid_until']})")
    return errs


def profit_sim(inputs: dict, business: dict, known_ids: set[str] | None = None, assumption_ids: set[str] | None = None,
               fee_errors: list[str] | None = None):
    """Mô phỏng 3 kịch bản. Thiếu bất kỳ đầu vào nào, hoặc phí sàn chưa xác minh -> status=incomplete, không xuất lợi nhuận."""
    if fee_errors:
        return {"status": "incomplete", "missing": [], "unverified_fees": fee_errors,
                "message": "Phí sàn chưa có biểu phí xác minh (nguồn + ngày + đúng ngành hàng). Đánh dấu missing, không kết luận lợi nhuận."}
    missing = [f for f in PROFIT_FIELDS if f not in inputs]
    unref = []
    for f in PROFIT_FIELDS:
        s = inputs.get(f)
        if s is None:
            continue
        ref = s.get("ref") if isinstance(s, dict) else None
        if not ref:
            unref.append(f)
        elif known_ids is not None and not ref_known(ref, known_ids, assumption_ids):
            unref.append(f)
    if missing:
        return {"status": "incomplete", "missing": missing, "unreferenced": unref,
                "message": "Chưa đủ chi phí, không kết luận lợi nhuận. Giá nhập thiếu -> ghi 'cần liên hệ báo giá'."}
    out = {}
    for sc in SCENARIOS:
        try:
            v = {f: _val(f, inputs[f], sc) for f in PROFIT_FIELDS}
        except KeyError as e:
            return {"status": "incomplete", "missing": [f"{e.args[0]}.{sc}"], "unreferenced": unref}
        p = v["sell_price"]
        landed = v["unit_cost"] + v["inbound_shipping"]
        fees = p * (v["platform_fee_pct"] + v["payment_fee_pct"]) / 100
        ads = p * v["ads_pct"] / 100
        returns = v["return_rate_pct"] / 100 * (landed * v["return_loss_pct"] / 100 + v["seller_shipping"])
        net = p - landed - fees - ads - v["seller_shipping"] - v["packaging"] - v["ops_per_unit"] - returns - v["other_per_unit"]
        moq = max(1, int(v["moq"]))
        test_capital = moq * landed + v["fixed_test_cost"]
        out[sc] = {
            "sell_price": round(p), "landed_cost": round(landed), "platform_fees": round(fees), "ads": round(ads),
            "returns_cost": round(returns), "gross_profit_per_unit": round(p - landed),
            "net_profit_per_unit": round(net), "net_margin_pct": round(100 * net / p, 1) if p else None,
            "breakeven_units": math.ceil(v["fixed_test_cost"] / net) if net > 0 else None,
            "test_capital": round(test_capital),
            "fits_test_capital": test_capital <= business["test_capital_vnd"],
        }
    n_ref = len(PROFIT_FIELDS) - len(unref)
    return {"status": "complete", "scenarios": out, "unreferenced": unref,
            "input_confidence": round(n_ref / len(PROFIT_FIELDS), 3),
            "test_capital_limit": business["test_capital_vnd"]}


def ref_known(ref: str, known_ids, assumption_ids) -> bool:
    """ref hợp lệ: user:..., assumption:<id>[+<id>], hoặc id bản ghi trong kho (nhiều id nối bằng '+')."""
    if ref.startswith("user:"):
        return True
    if ref.startswith("assumption:"):
        return assumption_ids is not None and all(x in assumption_ids for x in ref.split(":", 1)[1].split("+"))
    return all(x in known_ids for x in ref.split("+"))


def _quantile(xs, q):
    xs = sorted(xs)
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


MIN_PRICE_LISTINGS = 3


def price_stats(store, pid, platform=None):
    """Giá bán thị trường từ listing đối thủ (price_vnd). Kịch bản: thận trọng = phân vị 25, cơ sở = trung vị, lạc quan = phân vị 75."""
    ls = [r for r in store.records(pid, kind="listing") if isinstance(r.get("price_vnd"), (int, float)) and r["price_vnd"] > 0]
    if platform:
        ls = [r for r in ls if r.get("platform") == platform]
    prices = [float(r["price_vnd"]) for r in ls]
    res = {"product_id": pid, "n": len(prices), "listing_ids": [r["id"] for r in ls],
           "platforms": sorted({r.get("platform") for r in ls})}
    if len(prices) < MIN_PRICE_LISTINGS:
        res.update(status="insufficient", message=f"Cần ≥ {MIN_PRICE_LISTINGS} tin đăng có giá cùng biến thể; mới có {len(prices)}")
        return res
    res.update(status="ok", min=round(min(prices)), p25=round(_quantile(prices, .25)), median=round(_quantile(prices, .5)),
               p75=round(_quantile(prices, .75)), max=round(max(prices)))
    return res


COST_PROFILE_FIELDS = ["inbound_shipping", "ads_pct", "seller_shipping", "packaging", "ops_per_unit",
                       "return_rate_pct", "return_loss_pct", "other_per_unit", "fixed_test_cost"]
COST_BASES = ("dealer_quote", "wholesale_listed")


def _spec_from(value, ref):
    if isinstance(value, dict):
        return {**{s: value[s] for s in SCENARIOS if s in value}, "ref": ref}
    return {**{s: value for s in SCENARIOS}, "ref": ref}


def build_profit_inputs(store, pid, platform):
    """Ghép đầu vào lợi nhuận từ kho: giá bán thị trường, phí sàn người dùng chốt, hồ sơ chi phí (assumption cost_*),
    giá nhập từ báo giá đại lý / giá sỉ niêm yết trong nước. Không tự điền số nào; thiếu thì ghi vào missing."""
    amap = {a["id"]: a for a in store.assumptions()}
    usable = lambda a: a is not None and a.get("status") != "missing" and a.get("value") is not None
    inputs, sources, missing = {}, {}, []
    ps = price_stats(store, pid)
    if ps["status"] == "ok":
        inputs["sell_price"] = {"conservative": ps["p25"], "base": ps["median"], "optimistic": ps["p75"],
                                "ref": "+".join(ps["listing_ids"])}
        sources["sell_price"] = f"{ps['n']} tin đăng ({', '.join(ps['platforms'])}): p25/trung vị/p75"
    else:
        missing.append("sell_price")
    fee_id = f"{platform}_total_fee_pct_user"
    if usable(amap.get(fee_id)):
        inputs["platform_fee_pct"] = _spec_from(amap[fee_id]["value"], f"assumption:{fee_id}")
        inputs["payment_fee_pct"] = _spec_from(0, f"assumption:{fee_id}")
        sources["platform_fee_pct"] = f"assumption:{fee_id} (tổng phí, payment_fee_pct=0 để không tính trùng)"
    else:
        missing += ["platform_fee_pct", "payment_fee_pct"]
    for f in COST_PROFILE_FIELDS:
        a = amap.get(f"cost_{f}_{platform}") or amap.get(f"cost_{f}")
        if usable(a):
            inputs[f] = _spec_from(a["value"], f"assumption:{a['id']}")
            sources[f] = f"assumption:{a['id']}"
        else:
            missing.append(f)
    best = None
    for s in store.records(pid, kind="supplier"):
        if s.get("price_basis") not in COST_BASES or s.get("market", "VN") != "VN" or not s.get("price_tiers"):
            continue
        tier = min(s["price_tiers"], key=lambda x: x.get("min_qty", 1))
        if best is None or tier["price"] < best[1]["price"]:
            best = (s, tier)
    if best:
        s, tier = best
        inputs["unit_cost"] = _spec_from(tier["price"], s["id"])
        inputs["moq"] = _spec_from(max(1, int(tier.get("min_qty", 1))), s["id"])
        sources["unit_cost"] = f"{s['id']} ({s.get('name')}, {s['price_basis']})"
    else:
        missing += ["unit_cost", "moq"]
    return {"product_id": pid, "platform": platform, "inputs": inputs, "sources": sources,
            "missing": [f for f in PROFIT_FIELDS if f in missing], "price_stats": ps}


MAX_COST_FIELDS = [f for f in PROFIT_FIELDS if f not in ("unit_cost", "moq", "fixed_test_cost")]


def max_unit_cost(inputs: dict, targets=(10, 20), fee_errors=None):
    """Giá nhập tối đa (VND/sp) để biên lợi nhuận ròng còn đạt mục tiêu, cho từng kịch bản.
    Cùng công thức với profit_sim, giải ngược theo unit_cost."""
    if fee_errors:
        return {"status": "incomplete", "missing": [], "unverified_fees": fee_errors}
    missing = [f for f in MAX_COST_FIELDS if f not in inputs]
    if missing:
        return {"status": "incomplete", "missing": missing,
                "message": "Thiếu chi phí nên chưa tính được giá nhập tối đa."}
    out = {}
    for sc in SCENARIOS:
        try:
            v = {f: _val(f, inputs[f], sc) for f in MAX_COST_FIELDS}
        except KeyError as e:
            return {"status": "incomplete", "missing": [f"{e.args[0]}.{sc}"]}
        p, rr, rl = v["sell_price"], v["return_rate_pct"] / 100, v["return_loss_pct"] / 100
        pct = (v["platform_fee_pct"] + v["payment_fee_pct"] + v["ads_pct"]) / 100
        fixed = v["seller_shipping"] * (1 + rr) + v["packaging"] + v["ops_per_unit"] + v["other_per_unit"]
        row = {"sell_price": round(p)}
        for m in targets:
            landed = (p * (1 - pct - m / 100) - fixed) / (1 + rr * rl)
            u = math.floor(landed - v["inbound_shipping"])
            row[f"max_unit_cost_at_{m}pct"] = u if u > 0 else None
        out[sc] = row
    return {"status": "complete", "targets_pct": list(targets), "scenarios": out}


# ---------------------------------------------------------------- scoring

def score(store: Store, pid, scores: dict, opportunity_types=(), now=None):
    w = cfg("scoring_weights")
    crit = w["criteria"]
    unknown = set(scores) - set(crit)
    if unknown:
        raise APHError(f"Tiêu chí không hợp lệ: {unknown}")
    for t in opportunity_types:
        if t not in SCHEMA["enums"]["opportunity_type"]:
            raise APHError(f"opportunity_type không hợp lệ: {t}")
    conf = compute_confidence(store, pid, now)
    breakdown, raw, flags = {}, 0.0, []
    for c, meta in crit.items():
        s = scores.get(c, {})
        val = s.get("score") if isinstance(s, dict) else s
        if val is None:
            flags.append(f"{c}: chưa chấm (thiếu dữ liệu) -> 0 điểm")
            val = 0
        if not 0 <= val <= 10:
            raise APHError(f"{c}: score phải 0–10")
        cc = conf["criteria"][c]["confidence"]
        if val > 0 and cc == 0:
            flags.append(f"{c}: có điểm nhưng không có bằng chứng trong kho -> 0 điểm")
            val = 0
        pts = meta["weight"] * val / 10
        raw += pts
        breakdown[c] = {"score_0_10": val, "points": round(pts, 1), "max": meta["weight"], "confidence": cc,
                        "why": s.get("why") if isinstance(s, dict) else None,
                        "evidence": s.get("evidence", []) if isinstance(s, dict) else []}
    weak_critical = [c for c, m in crit.items() if m["critical"] and conf["criteria"][c]["confidence"] < w["critical_confidence_min"]]
    total = raw
    cap_reason = None
    if weak_critical and raw > w["cap_when_critical_unverified"]:
        total = w["cap_when_critical_unverified"]
        cap_reason = f"Dữ liệu trọng yếu chưa đủ tin cậy: {weak_critical}"
    band = next(b["label"] for b in w["bands"] if total >= b["min"])
    ev = {"product_id": pid, "weights_version": w["version"], "raw_total": round(raw, 1), "total": round(total, 1),
          "band": band, "data_confidence": conf["data_confidence"], "confidence_detail": conf["criteria"],
          "breakdown": breakdown, "cap_reason": cap_reason, "weak_critical": weak_critical,
          "opportunity_types": list(opportunity_types), "flags": flags}
    return ev


def gate(ev: dict):
    w = cfg("scoring_weights")
    t, dc = ev["total"], ev["data_confidence"]
    if t >= w["qualified_min_score"] and dc >= w["qualified_min_data_confidence"] and not ev["cap_reason"]:
        s, why = "qualified_opportunity", "Điểm và độ tin cậy dữ liệu đạt ngưỡng"
    elif ev["cap_reason"] or t >= 60:
        s, why = "research_more", ev["cap_reason"] or "Điểm khá nhưng cần thêm dữ liệu"
    elif t >= 40:
        s, why = "watchlist", "Tín hiệu chưa đủ, theo dõi theo thời gian"
    else:
        s, why = "rejected", "Điểm thấp. Chỉ chuyển Rejected khi có lý do cụ thể được ghi kèm"
    return {"suggested_research_status": s, "reason": why, "total": t, "data_confidence": dc}


# ---------------------------------------------------------------- index

def reindex(store: Store):
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(tmp)
    con.executescript("""
        CREATE TABLE products(product_id TEXT PRIMARY KEY, canonical_name TEXT, category TEXT, function_key TEXT, json TEXT);
        CREATE TABLE records(id TEXT PRIMARY KEY, kind TEXT, product_id TEXT, run_id TEXT, collected_at TEXT, metric TEXT, json TEXT);
        CREATE INDEX rp ON records(product_id, kind, collected_at);
    """)
    ps = store.products()
    con.executemany("INSERT INTO products VALUES(?,?,?,?,?)",
                    [(p["product_id"], p["canonical_name"], p["category"], p["function_key"], json.dumps(p, ensure_ascii=False)) for p in ps])
    rs = store.records()
    con.executemany("INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?)",
                    [(r["id"], r["kind"], r.get("product_id"), r["run_id"], r["collected_at"], r.get("metric"), json.dumps(r, ensure_ascii=False)) for r in rs])
    con.commit()
    con.close()
    dest = store.root / "index.sqlite"
    shutil.copyfile(tmp, dest)
    os.unlink(tmp)
    return {"products": len(ps), "records": len(rs), "index": str(dest)}


def duplicates(store: Store):
    ps = store.products()
    out = []
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            if a["function_key"] == b["function_key"]:
                out.append({"a": a["product_id"], "b": b["product_id"], "reason": "same function_key"})
            else:
                s = similarity(a["canonical_name"], b["canonical_name"])
                if s >= 0.8:
                    out.append({"a": a["product_id"], "b": b["product_id"], "reason": f"name similarity {s}"})
    return out


VENDOR_CRITERIA = ["legal_entity", "address", "invoice", "warranty", "supply_capacity", "moq_payment"]
VENDOR_LABELS = {"legal_entity": "Pháp nhân", "address": "Địa chỉ", "invoice": "Xuất hóa đơn", "warranty": "Bảo hành",
                 "supply_capacity": "Khả năng cung ứng", "moq_payment": "MOQ và thanh toán"}
VENDOR_POINTS = {"verified": 2, "user_input": 2, "stated": 1, "missing": 0}


def vendor_assessment(rec):
    """Chấm 1 bản đánh giá NCC: verified/user_input = 2, NCC tự ghi = 1, thiếu = 0; tối đa 12.
    Mức 'high' cần pháp nhân đã đối chiếu (verified/user_input) và không thiếu tiêu chí nào."""
    crit = rec["criteria"]
    score = sum(VENDOR_POINTS[crit[c]["status"]] for c in VENDOR_CRITERIA)
    missing = [c for c in VENDOR_CRITERIA if crit[c]["status"] == "missing"]
    legal_ok = crit["legal_entity"]["status"] in ("verified", "user_input")
    if score >= 9 and legal_ok and not missing:
        level = "high"
    elif score >= 6 and crit["legal_entity"]["status"] != "missing":
        level = "medium"
    else:
        level = "low"
    return {"vendor_key": rec["vendor_key"], "vendor_name": rec["vendor_name"], "review_id": rec["id"], "score": score, "max": 12,
            "level": level, "legal_entity_confirmed": legal_ok,
            "ask_vendor": [VENDOR_LABELS[c] for c in missing],
            "only_self_stated": [VENDOR_LABELS[c] for c in VENDOR_CRITERIA if crit[c]["status"] == "stated"]}


def list_vendors(store: Store):
    latest = {}
    for r in store.records(kind="vendor_review"):
        latest[r["vendor_key"]] = r
    return sorted((vendor_assessment(r) for r in latest.values()), key=lambda x: -x["score"])


def list_problems(store: Store):
    recs = store.records()
    out = []
    for p in [r for r in recs if r["kind"] == "problem"]:
        sols = [{"product_id": r["product_id"], "fit": r["fit"], "bundle_with": r.get("bundle_with", [])}
                for r in recs if r["kind"] == "solution" and r["problem_ref"] == p["id"]]
        dem = [r for r in recs if r["kind"] == "demand" and r.get("problem_ref") == p["id"]]
        ev_ids = set(p.get("evidence", [])) | {r["id"] for r in recs if r.get("problem_ref") == p["id"] and r["kind"] in ("observation", "listing")}
        ev = [r for r in recs if r["id"] in ev_ids]
        out.append({"id": p["id"], "statement": p["statement"], "category": p["category"], "status": p.get("status", "candidate"),
                    "evidence_count": len(ev), "independent_sources": len({platform_of(r) for r in ev}),
                    "demand_signals": {t: sum(1 for d in dem if d["type"] == t) for t in sorted({d["type"] for d in dem})},
                    "solutions": sols, "solution_gaps": p.get("solution_gaps", [])})
    return out


def platform_of(r):
    if r.get("platform"):
        return r["platform"]
    return domain(r.get("source_url") or r.get("url"))


TREND_ATTENTION = ("search_interest", "csi_popularity", "content_signal")


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2) if n else 0


def trend_indicator(series, window=12):
    """Phân loại một chuỗi theo tuần (giá trị tương đối 0–100) bằng trung vị để không bị một tuần đột biến kéo lệch.
    So trung vị `window` tuần gần nhất với `window` tuần trước đó."""
    pts = sorted((p for p in series if isinstance(p, dict) and p.get("t") is not None and p.get("v") is not None),
                 key=lambda p: str(p["t"]))
    vals = [float(p["v"]) for p in pts]
    # Chuỗi số tuyệt đối (ví dụ TikTok CSI cộng theo tuần) được quy về 0–100 như Trends để ngưỡng ít dữ liệu dùng chung được.
    scale = 100.0 / max(vals) if vals and max(vals) > 100 else 1.0
    vals = [v * scale for v in vals]
    if len(vals) < 2 * window:
        return {"stage": "unknown", "reason": "too_few_points", "points": len(vals)}
    nonzero = sum(v > 0 for v in vals) / len(vals)
    if max(vals) < 10 or nonzero < 0.5:
        return {"stage": "unknown", "reason": "low_volume", "points": len(vals), "nonzero_share": round(nonzero, 2)}
    recent, prior = _median(vals[-window:]), _median(vals[-2 * window:-window])
    base, early = _median(vals), _median(vals[:window])
    if max(recent, prior) < 10:
        return {"stage": "unknown", "reason": "low_volume", "points": len(vals), "recent_median": recent, "prior_median": prior}
    g = (recent - prior) / max(prior, 1.0)
    if g >= 0.3:
        stage = "emerging" if early <= 0.2 * recent else ("trending" if recent >= 1.5 * base else "growing")
    elif g <= -0.3:
        stage = "declining"
    else:
        stage = "stable"
    return {"stage": stage, "growth": round(g, 3), "recent_median": recent, "prior_median": prior,
            "year_median": base, "first_window_median": early, "points": len(vals), "from": str(pts[0]["t"])[:10], "to": str(pts[-1]["t"])[:10]}


DIRECTION = {"emerging": 1, "growing": 1, "trending": 1, "stable": 0, "declining": -1}


def trend_assess(store, pid):
    """Gộp chỉ báo theo từng nguồn. Kết luận giai đoạn chỉ khi ≥ 2 nguồn độc lập có tín hiệu và cùng hướng (rules/research_rules.md mục 3)."""
    obs = [r for r in store.records(pid) if r["kind"] == "observation" and r["metric"] in TREND_ATTENTION and r.get("series")]
    per = []
    for r in obs:
        ind = trend_indicator(r["series"])
        per.append({"id": r["id"], "source": platform_of(r), "metric": r["metric"], "term": r.get("note", "")[:80], **ind})
    usable = {}
    for x in per:
        if x["stage"] != "unknown":
            usable.setdefault(x["source"], x)
    dirs = {DIRECTION[x["stage"]] for x in usable.values()}
    if len(usable) >= 2 and len(dirs) == 1:
        stages = {x["stage"] for x in usable.values()}
        # Các nguồn cùng hướng nhưng khác mức (ví dụ growing và trending): chọn mức thận trọng nhất của hướng đó.
        stage = stages.pop() if len(stages) == 1 else {1: "growing", 0: "stable", -1: "declining"}[dirs.pop()]
        basis = f"{len(usable)} nguồn cùng hướng: " + ", ".join(f"{k}={v['stage']}" for k, v in usable.items())
    elif len(usable) >= 2:
        stage, basis = "unknown", "Các nguồn mâu thuẫn: " + ", ".join(f"{k}={v['stage']}" for k, v in usable.items())
    elif len(usable) == 1:
        (k, v), = usable.items()
        stage, basis = "unknown", f"Chỉ 1 nguồn có tín hiệu ({k}: {v['stage']}, tăng {v['growth']:+.0%} so với 12 tuần trước). Cần thêm nguồn độc lập"
    else:
        reasons = sorted({x.get("reason", "") for x in per}) or ["no_series"]
        stage, basis = "unknown", "Không có chuỗi đủ dữ liệu: " + ", ".join(reasons)
    return {"product_id": pid, "stage": stage, "basis": basis, "per_series": per,
            "evidence": [x["id"] for x in per], "timepoints": max((x.get("points", 0) for x in per), default=0)}


def coverage(store: Store, pid):
    """Nhu cầu dữ liệu nào đã có bằng chứng từ bao nhiêu nền tảng; nguồn nào còn thiếu hoặc bị chặn."""
    fb = cfg("source_fallbacks")["needs"]
    allrecs = store.records()
    probs = store.problems_for(pid, allrecs)
    recs = [r for r in allrecs if r.get("product_id") == pid or r.get("problem_ref") in probs]
    blocked = {}
    for r in allrecs:
        if r["kind"] == "access" and r["status"] != "ok":
            blocked[domain(r["url"])] = r["status"]
    out = {}
    for need, spec in fb.items():
        items = [r for r in recs if (r["kind"] == "observation" and r["metric"] in spec["metrics"]) or r["kind"] in spec["kinds"]]
        plats = sorted({p for p in (platform_of(r) for r in items) if p})
        extra = {}
        if need == "supply":
            # Giá bán lẻ hay giá gốc 1688 không phải giá nhập: chỉ giá sỉ niêm yết hoặc báo giá đại lý trong nước mới tính.
            cost = [r for r in items if r["kind"] == "supplier" and r.get("market", "VN") == "VN"
                    and r.get("price_basis") in ("wholesale_listed", "dealer_quote")]
            extra = {"cost_price_known": bool(cost), "cost_price_suppliers": [r["id"] for r in cost],
                     "need_quote": [r["id"] for r in items if r["kind"] == "supplier" and r.get("quote_status") == "need_quote"]}
        out[need] = {**extra, "platforms": plats, "covered": len(plats) >= spec["min_platforms"] if spec["min_platforms"] else None,
                     "shopee_present": any("shopee" in p for p in plats),
                     "next_sources": [s for s, mode in spec["sources"] if not any(s in p for p in plats)]}
    return {"product_id": pid, "needs": out, "blocked_sources_seen": blocked}


def show(store: Store, pid):
    p = store.product(pid)
    recs = store.records(pid)
    current, history = {}, {}
    for r in recs:
        if r["kind"] == "observation":
            key = f"{r['metric']}@{r.get('platform') or r.get('market') or '-'}"
            history.setdefault(key, []).append({"value": r["value"], "at": r.get("observed_at") or r["collected_at"], "id": r["id"]})
            current[key] = {"value": r["value"], "unit": r.get("unit"), "verification": r["verification"],
                            "source_url": r.get("source_url"), "at": r.get("observed_at") or r["collected_at"], "id": r["id"]}
    counts = {}
    for r in recs:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    evals = [r for r in recs if r["kind"] == "evaluation"]
    profits = [r for r in recs if r["kind"] == "profit"]
    return {"product": p, "status": store.status(pid), "current": current,
            "history_points": {k: len(v) for k, v in history.items()}, "record_counts": counts,
            "latest_evaluation": evals[-1] if evals else None, "latest_profit": profits[-1] if profits else None}


def list_products(store: Store):
    # Quét kho đúng một lần rồi nhóm theo sản phẩm (trước đây quét lại cho từng sản phẩm: O(P x R)).
    rows, evals, decisions = [], {}, {}
    for r in store.records():
        if r["kind"] == "evaluation":
            evals[r["product_id"]] = r
        elif r["kind"] == "decision":
            decisions.setdefault(r["product_id"], []).append(r)
    for p in store.products():
        e = evals.get(p["product_id"])
        rows.append({"product_id": p["product_id"], "name": p["canonical_name"], "category": p["category"],
                     **store.status(p["product_id"], decisions.get(p["product_id"], [])),
                     "score": e["total"] if e else None, "data_confidence": e["data_confidence"] if e else None,
                     "evaluated_at": e["collected_at"] if e else None})
    return sorted(rows, key=lambda r: -(r["score"] or -1))


# ---------------------------------------------------------------- CLI

def read_json_arg(s: str):
    if s.startswith("@"):
        return json.loads(Path(s[1:]).read_text(encoding="utf-8"))
    return json.loads(s)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aph", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.environ.get("APH_DATA", DEFAULT_DATA))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    s = sub.add_parser("new-run"); s.add_argument("--type", default="manual"); s.add_argument("--request", required=True)
    s.add_argument("--depth", default="standard"); s.add_argument("--plan", default="[]", help="JSON list các agent")
    s = sub.add_parser("finish-run"); s.add_argument("--run", required=True); s.add_argument("--summary", required=True)
    s.add_argument("--agents", default="[]")
    s = sub.add_parser("match"); s.add_argument("--name", required=True); s.add_argument("--function-key"); s.add_argument("--aliases", default="[]")
    s = sub.add_parser("add-product"); s.add_argument("--run", required=True); s.add_argument("--json", required=True, help="JSON hoặc @file")
    s.add_argument("--force-new", action="store_true")
    s = sub.add_parser("relate"); s.add_argument("--product", required=True); s.add_argument("--rel", required=True); s.add_argument("--other", required=True)
    s = sub.add_parser("add", help="Thêm bản ghi: observation, listing, supplier, demand, trend, decision, outcome, watch, access")
    s.add_argument("--run", required=True); s.add_argument("--kind", required=True); s.add_argument("--json", required=True)
    s = sub.add_parser("log-access"); s.add_argument("--run", required=True); s.add_argument("--url", required=True)
    s.add_argument("--status", required=True); s.add_argument("--agent"); s.add_argument("--note")
    s = sub.add_parser("retract", help="Rút lại bản ghi sai (ghi thêm bản ghi retraction, không xóa)")
    s.add_argument("--run", required=True); s.add_argument("--id", required=True); s.add_argument("--reason", required=True)
    s = sub.add_parser("profit"); s.add_argument("--run"); s.add_argument("--product", required=True); s.add_argument("--inputs", required=True)
    s.add_argument("--platform", required=True, choices=["shopee", "tiktok_shop", "lazada", "own_site"])
    s.add_argument("--dry", action="store_true", help="Chỉ tính, không ghi")
    s = sub.add_parser("prices", help="Giá bán thị trường (p25/trung vị/p75) từ listing đối thủ"); s.add_argument("--product", required=True)
    s.add_argument("--platform")
    s = sub.add_parser("profit-inputs", help="Ghép đầu vào lợi nhuận từ kho, liệt kê mục còn thiếu")
    s.add_argument("--product", required=True); s.add_argument("--platform", required=True, choices=["shopee", "tiktok_shop", "lazada", "own_site"])
    s = sub.add_parser("max-cost", help="Giá nhập tối đa để còn lãi ròng theo mục tiêu (%)"); s.add_argument("--run")
    s.add_argument("--product", required=True); s.add_argument("--platform", required=True, choices=["shopee", "tiktok_shop", "lazada", "own_site"])
    s.add_argument("--inputs", help="JSON hoặc @file; bỏ trống = ghép từ kho (profit-inputs)"); s.add_argument("--targets", default="10,20")
    s.add_argument("--dry", action="store_true")
    s = sub.add_parser("confidence"); s.add_argument("--product", required=True)
    s = sub.add_parser("score"); s.add_argument("--run"); s.add_argument("--product", required=True); s.add_argument("--scores", required=True)
    s.add_argument("--types", default="[]"); s.add_argument("--dry", action="store_true")
    s = sub.add_parser("gate"); s.add_argument("--product", required=True)
    s = sub.add_parser("set-status"); s.add_argument("--run", required=True); s.add_argument("--product", required=True)
    s.add_argument("--research"); s.add_argument("--business"); s.add_argument("--reason", required=True); s.add_argument("--by", default="user")
    s = sub.add_parser("show"); s.add_argument("--product", required=True)
    sub.add_parser("list")
    sub.add_parser("duplicates")
    sub.add_parser("vendors", help="Đánh giá nhà cung cấp theo 6 tiêu chí (bản mới nhất mỗi NCC)")
    sub.add_parser("problems", help="Liệt kê vấn đề khách hàng và sản phẩm/bộ giải pháp gắn với từng vấn đề")
    s = sub.add_parser("trend", help="Phân loại giai đoạn xu hướng từ chuỗi theo tuần; ghi bản ghi trend trừ khi --dry")
    s.add_argument("--run"); s.add_argument("--product", required=True); s.add_argument("--dry", action="store_true")
    s = sub.add_parser("coverage", help="Độ phủ nguồn theo nhu cầu dữ liệu (config/source_fallbacks.json)"); s.add_argument("--product", required=True)
    sub.add_parser("reindex")

    a = ap.parse_args(argv)
    st = Store(a.data)
    try:
        if a.cmd == "init":
            st.init(); res = {"ok": True, "data": str(st.root)}
        elif a.cmd == "new-run":
            res = st.new_run(a.type, a.request, a.depth, json.loads(a.plan))
        elif a.cmd == "finish-run":
            res = st.finish_run(a.run, a.summary, json.loads(a.agents))
        elif a.cmd == "match":
            res = st.match(a.name, a.function_key, json.loads(a.aliases))
        elif a.cmd == "add-product":
            res = st.add_product(a.run, read_json_arg(a.json), a.force_new)
        elif a.cmd == "relate":
            res = st.relate(a.product, a.rel, a.other)
        elif a.cmd == "add":
            res = st.append(a.run, a.kind, read_json_arg(a.json))
        elif a.cmd == "log-access":
            d = {"url": a.url, "status": a.status}
            if a.agent: d["agent"] = a.agent
            if a.note: d["note"] = a.note
            res = st.append(a.run, "access", d)
        elif a.cmd == "retract":
            res = st.retract(a.run, a.id, a.reason)
        elif a.cmd == "profit":
            prod = st.product(a.product)
            known = {r["id"] for r in st.records(a.product)}
            amap = {x["id"]: x for x in st.assumptions()}
            biz = cfg("business_profile")
            inp = read_json_arg(a.inputs) if a.inputs != "auto" else build_profit_inputs(st, a.product, a.platform)["inputs"]
            cat = (prod.get("platform_categories") or {}).get(a.platform)
            fee_errs = check_fee_refs(inp, amap, a.platform, cat, (biz.get("shop_type") or {}).get(a.platform))
            res = profit_sim(inp, biz, known, set(amap), fee_errs)
            res["platform"] = a.platform
            if not a.dry:
                if not a.run:
                    raise APHError("Cần --run để ghi kết quả (hoặc dùng --dry)")
                res = st.append(a.run, "profit", {"product_id": a.product, "inputs": inp, **res}, validate_kind=False)
        elif a.cmd == "prices":
            st.product(a.product); res = price_stats(st, a.product, a.platform)
        elif a.cmd == "profit-inputs":
            st.product(a.product); res = build_profit_inputs(st, a.product, a.platform)
        elif a.cmd == "max-cost":
            prod = st.product(a.product)
            amap = {x["id"]: x for x in st.assumptions()}
            biz = cfg("business_profile")
            built = None if a.inputs else build_profit_inputs(st, a.product, a.platform)
            inp = read_json_arg(a.inputs) if a.inputs else built["inputs"]
            cat = (prod.get("platform_categories") or {}).get(a.platform)
            fee_errs = check_fee_refs(inp, amap, a.platform, cat, (biz.get("shop_type") or {}).get(a.platform))
            res = max_unit_cost(inp, [int(x) if float(x).is_integer() else float(x) for x in a.targets.split(",")], fee_errs)
            res["platform"] = a.platform
            if built:
                res["sources"] = built["sources"]
            if not a.dry:
                if not a.run:
                    raise APHError("Cần --run để ghi kết quả (hoặc dùng --dry)")
                res = st.append(a.run, "max_cost", {"product_id": a.product, "inputs": inp, **res}, validate_kind=False)
        elif a.cmd == "confidence":
            st.product(a.product); res = compute_confidence(st, a.product)
        elif a.cmd == "score":
            st.product(a.product)
            ev = score(st, a.product, read_json_arg(a.scores), json.loads(a.types))
            ev["gate"] = gate(ev)
            if a.dry:
                res = ev
            else:
                if not a.run:
                    raise APHError("Cần --run để ghi đánh giá (hoặc dùng --dry)")
                res = st.append(a.run, "evaluation", ev, validate_kind=False)
        elif a.cmd == "gate":
            evs = st.records(a.product, "evaluation")
            if not evs:
                raise APHError("Chưa có evaluation cho sản phẩm này")
            res = gate(evs[-1])
        elif a.cmd == "set-status":
            d = {"product_id": a.product, "reason": a.reason, "by": a.by}
            if a.research: d["research_status"] = a.research
            if a.business: d["business_status"] = a.business
            if not (a.research or a.business):
                raise APHError("Cần --research hoặc --business")
            res = st.append(a.run, "decision", d)
        elif a.cmd == "show":
            res = show(st, a.product)
        elif a.cmd == "list":
            res = list_products(st)
        elif a.cmd == "problems":
            res = list_problems(st)
        elif a.cmd == "vendors":
            res = list_vendors(st)
        elif a.cmd == "trend":
            res = trend_assess(st, a.product)
            if not a.dry:
                if not a.run:
                    raise APHError("trend cần --run (hoặc --dry)")
                if not res["evidence"]:
                    raise APHError("Không có observation chuỗi (series) nào cho sản phẩm này")
                res["record"] = st.append(a.run, "trend", {"product_id": a.product, "stage": res["stage"], "basis": res["basis"],
                                                          "evidence": res["evidence"], "timepoints": res["timepoints"]})["id"]
        elif a.cmd == "coverage":
            st.product(a.product); res = coverage(st, a.product)
        elif a.cmd == "duplicates":
            res = duplicates(st)
        elif a.cmd == "reindex":
            res = reindex(st)
    except APHError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
