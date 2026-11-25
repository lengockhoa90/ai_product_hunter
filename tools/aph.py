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
        if kind == "variant_link":
            self._check_variant_link(data)
            prev = variant_links(self.records(kind="variant_link")).get(data["target"])
            if prev and prev["variant_key"] == data["variant_key"]:
                return {**prev, "status": "existing"}  # chạy lại không tạo bản ghi trùng
        if kind == "variant_profile":
            self._check_variant_profile(data)
            prev = variant_profiles(self.records(kind="variant_profile")).get((data["product_id"], data["variant_key"]))
            if prev and all(prev.get(k) == data.get(k) for k in data):
                return {**prev, "status": "existing"}
        rec = {"id": f"{kind[:3]}_{uuid.uuid4().hex[:12]}", "kind": kind, "run_id": run_id,
               "collected_at": now_iso(), **data}
        path = self.records_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def _check_variant_link(self, d):
        """Gán model cho một bản ghi cũ mà không sửa nó. Đoạn trích phải có nguyên văn trong chính bản ghi đích."""
        t = next((r for r in self.records() if r["id"] == d["target"]), None)
        if t is None:
            raise APHError(f"variant_link: không có bản ghi {d['target']} (hoặc đã bị rút lại)")
        if t["kind"] not in LINKABLE_KINDS:
            raise APHError(f"variant_link: chỉ gán model cho {LINKABLE_KINDS}, không cho '{t['kind']}'")
        if t.get("product_id") != d["product_id"]:
            raise APHError(f"variant_link: {d['target']} thuộc sản phẩm {t.get('product_id')}, không phải {d['product_id']}")
        own = t.get("variant_key") or (t.get("variant") if t["kind"] in ("max_cost", "profit") else None)
        if own and own != d["variant_key"]:
            raise APHError(f"variant_link: {d['target']} đã có variant_key '{own}', không gán sang model khác")
        ex = norm_text(d["evidence_excerpt"])
        if len(ex) < 4 or ex not in rec_text(t):
            raise APHError("variant_link: evidence_excerpt phải chép nguyên văn từ tên/tiêu đề/ghi chú/đoạn trích của bản ghi đích")
        if len(d["reason"].strip()) < 8:
            raise APHError("variant_link: cần reason giải thích vì sao bản ghi này thuộc model đó")

    def _check_variant_profile(self, d):
        """Hồ sơ model: thương hiệu, tên model, giao thức phải đọc được trong chính bản ghi bằng chứng của đúng model."""
        recs = {r["id"]: r for r in self.records()}
        links = variant_links(recs.values())
        if not d["evidence"]:
            raise APHError("variant_profile cần evidence (id tin đăng/NCC của đúng model)")
        evs = []
        for e in d["evidence"]:
            r = recs.get(e)
            if r is None or r.get("product_id") != d["product_id"]:
                raise APHError(f"variant_profile: evidence {e} không có hoặc không thuộc sản phẩm {d['product_id']}")
            if record_variant(r, links) != d["variant_key"]:
                raise APHError(f"variant_profile: evidence {e} không gắn model '{d['variant_key']}'")
            evs.append(rec_text(r))
        blob = " ".join(evs)
        if norm_text(d["evidence_excerpt"]) not in blob or len(norm_text(d["evidence_excerpt"])) < 4:
            raise APHError("variant_profile: evidence_excerpt phải chép nguyên văn từ một bản ghi bằng chứng")
        if not d["brand"].lower().startswith("không rõ") and norm_text(d["brand"]) not in blob:
            raise APHError(f"variant_profile: thương hiệu '{d['brand']}' không xuất hiện trong bằng chứng (ghi 'Không rõ …' nếu không có)")
        toks = PROTOCOL_TOKENS.get(d["protocol"])
        if toks and not any(tok in blob.split() for tok in toks):
            raise APHError(f"variant_profile: giao thức '{d['protocol']}' không được ghi trong bằng chứng; dùng 'unknown' nếu chưa rõ")
        if d["protocol"] in ("mixed", "wifi_zigbee") and not d.get("is_group"):
            raise APHError("variant_profile: nhiều giao thức thì là nhóm nhiều mẫu: đặt is_group=true và group_note")

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


def price_stats(store, pid, platform=None, variant=None):
    """Giá bán thị trường từ listing đối thủ (price_vnd). Kịch bản: thận trọng = phân vị 25, cơ sở = trung vị, lạc quan = phân vị 75.
    variant: chỉ lấy listing có variant_key này (cùng model/biến thể, ví dụ 'hunonic-datic-2nut')."""
    recs = store.records(pid, kind="listing")
    links = variant_links(store.records(pid, kind="variant_link"))
    if variant:
        recs = [r for r in recs if record_variant(r, links) == variant]
    own = [norm_text(x) for x in cfg("scoring_weights_v2").get("own_shop_names", [])]
    # mỗi tin đăng tính một lần (bản mới nhất), bỏ shop của anh Khoa, bỏ tin nhiều phân loại (giá thẻ là phân loại rẻ nhất)
    ls = [r for r in latest_by_listing(recs).values() if isinstance(r.get("price_vnd"), (int, float)) and r["price_vnd"] > 0
          and not any(o and o in norm_text(r.get("shop_name") or "") for o in own)]
    excluded_multi = [r["id"] for r in ls if variant and listing_sku_scope(r, links) == "multi_sku"]
    ls = [r for r in ls if r["id"] not in excluded_multi]
    if platform:
        ls = [r for r in ls if r.get("platform") == platform]
    prices = [float(r["price_vnd"]) for r in ls]
    res = {"product_id": pid, "variant": variant, "n": len(prices), "listing_ids": [r["id"] for r in ls], "excluded_multi_sku": excluded_multi,
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
COST_LABEL = {"inbound_shipping": "vận chuyển hàng về kho", "ads_pct": "quảng cáo %", "seller_shipping": "ship shop chịu", "packaging": "đóng gói",
              "ops_per_unit": "vận hành", "return_rate_pct": "tỷ lệ hoàn", "return_loss_pct": "tổn thất mỗi đơn hoàn", "other_per_unit": "chi phí khác",
              "fixed_test_cost": "chi phí cố định khi test"}
PCT_OF_PRICE_FIELDS = ("packaging", "ops_per_unit", "other_per_unit", "seller_shipping")


def _spec_from(value, ref):
    if isinstance(value, dict):
        return {**{s: value[s] for s in SCENARIOS if s in value}, "ref": ref}
    return {**{s: value for s in SCENARIOS}, "ref": ref}


def build_profit_inputs(store, pid, platform, variant=None):
    """Ghép đầu vào lợi nhuận từ kho: giá bán thị trường, phí sàn người dùng chốt, hồ sơ chi phí (assumption cost_*),
    giá nhập từ báo giá đại lý / giá sỉ niêm yết trong nước. Không tự điền số nào; thiếu thì ghi vào missing."""
    amap = {a["id"]: a for a in store.assumptions()}
    usable = lambda a: a is not None and a.get("status") != "missing" and a.get("value") is not None
    inputs, sources, missing = {}, {}, []
    # Giá bán lấy trên đúng sàn đang tính phí; chỉ khi sàn đó chưa đủ tin mới gộp mọi sàn (ghi rõ trong sources)
    ps = price_stats(store, pid, platform=platform, variant=variant)
    if ps["status"] != "ok":
        ps = price_stats(store, pid, variant=variant)
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
        if usable(a) and a.get("value_basis") == "pct_of_price":
            # chi phí người dùng cho theo % giá bán (đóng gói, vận hành…): quy ra VND/sp theo giá bán của từng kịch bản
            if f in PCT_OF_PRICE_FIELDS and "sell_price" in inputs:
                inputs[f] = {sc: round(inputs["sell_price"][sc] * a["value"] / 100) for sc in SCENARIOS} | {"ref": f"assumption:{a['id']}"}
                sources[f] = f"assumption:{a['id']} ({a['value']}% giá bán)"
            else:
                missing.append(f)
        elif usable(a):
            inputs[f] = _spec_from(a["value"], f"assumption:{a['id']}")
            sources[f] = f"assumption:{a['id']}"
        else:
            missing.append(f)
    best = None
    links = variant_links(store.records(kind="variant_link")) if variant else {}
    for s in store.records(pid, kind="supplier"):
        if variant and record_variant(s, links) != variant:
            continue  # chỉ lấy giá nhập của NCC gắn đúng model (không lấy giá model khác)
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
    return {"product_id": pid, "platform": platform, "variant": variant, "inputs": inputs, "sources": sources,
            "missing": [f for f in PROFIT_FIELDS if f in missing], "price_stats": ps}


MAX_COST_FIELDS = [f for f in PROFIT_FIELDS if f not in ("unit_cost", "moq", "fixed_test_cost")]


MAX_COST_REQUIRED = ["sell_price", "platform_fee_pct", "payment_fee_pct"]


def max_unit_cost(inputs: dict, targets=(10, 20), fee_errors=None, strict=False):
    """Giá nhập tối đa (VND/sp) để biên lợi nhuận ròng còn đạt mục tiêu, cho từng kịch bản.
    Cùng công thức với profit_sim, giải ngược theo unit_cost.
    Bắt buộc có giá bán và phí sàn. Chi phí shop chưa có (quảng cáo, ship, đóng gói, hoàn hàng…) được tính là 0
    và liệt kê trong excluded_costs -> status=partial: kết quả là TRẦN giá nhập, người dùng phải trừ tiếp các chi phí đó.
    strict=True: thiếu bất kỳ chi phí nào -> incomplete."""
    if fee_errors:
        return {"status": "incomplete", "missing": [], "unverified_fees": fee_errors}
    missing = [f for f in (MAX_COST_FIELDS if strict else MAX_COST_REQUIRED) if f not in inputs]
    if missing:
        return {"status": "incomplete", "missing": missing,
                "message": "Thiếu giá bán thị trường hoặc phí sàn nên chưa tính được giá nhập tối đa."}
    excluded = [f for f in MAX_COST_FIELDS if f not in inputs]
    inputs = {**inputs, **{f: 0 for f in excluded}}
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
    res = {"status": "partial" if excluded else "complete", "targets_pct": list(targets), "scenarios": out,
           "excluded_costs": excluded}
    if excluded:
        res["message"] = ("Trần giá nhập: chưa trừ " + ", ".join(excluded) +
                          ". Giá nhập chấp nhận được thực tế phải thấp hơn, trừ thêm các chi phí này.")
    return res


def discount_needed(mc: dict, retail_price: float, ref: str):
    """Chiết khấu trên giá lẻ đề xuất cần xin để giá nhập không vượt mức tối đa: 1 - giá nhập tối đa / giá lẻ (%)."""
    if mc.get("status") not in ("complete", "partial"):
        return None
    out = {"retail_price": retail_price, "ref": ref}
    for sc, row in mc["scenarios"].items():
        out[sc] = {k.replace("max_unit_cost", "min_discount_pct"): (round(100 * (1 - v / retail_price), 1) if v is not None else None)
                   for k, v in row.items() if k.startswith("max_unit_cost")}
    return out


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


def trend_assess(store, pid, variant=None):
    """Gộp chỉ báo theo từng nguồn. Kết luận giai đoạn chỉ khi ≥ 2 nguồn độc lập có tín hiệu và cùng hướng (rules/research_rules.md mục 3).
    variant=None: chỉ chuỗi cấp sản phẩm (không variant_key); variant=<key>: chỉ chuỗi đã gắn model đó (không trộn 2 cấp).
    Mỗi nguồn lấy chuỗi có confidence cao nhất."""
    noisy = cfg("noisy_series")["excluded"]
    obs = [r for r in store.records(pid) if r["kind"] == "observation" and r["metric"] in TREND_ATTENTION and r.get("series")
           and r.get("variant_key") == variant and r["id"] not in noisy]
    obs.sort(key=lambda r: -(r.get("confidence") or 0))
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
    return {"product_id": pid, "variant_key": variant, "stage": stage, "basis": basis, "per_series": per,
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


# ---------------------------------------------------------------- Agent 07: Opportunity Evaluator (chấm điểm v2)

MODEL_KINDS = ("listing", "supplier", "max_cost", "profit")
MODEL_METRICS = ("sold_label", "price", "unit_cost", "review_count", "seller_count", "listing_count", "moq")
LINKABLE_KINDS = ("listing", "supplier", "observation", "demand", "max_cost", "profit")
GROUPS = ("demand", "trend", "competition", "supplier", "financial")
JUDGEABLE = ("demand", "competition")
STATUS_LABEL = {"GO": "GO", "CONDITIONAL_GO": "CONDITIONAL GO", "NEED_MORE_DATA": "NEED MORE DATA", "NO_GO": "NO GO"}
SKIP_KINDS = ("variant_link", "evaluation", "demand_analysis", "competitor_analysis", "retraction", "note", "decision", "access", "watch", "outcome")


def rec_text(r):
    parts = [r.get(k) for k in ("name", "title", "note", "evidence_excerpt", "url", "shop_name")]
    return norm_text(" ".join(p for p in parts if isinstance(p, str)))


def variant_links(allrecs):
    """Bản ghi variant_link mới nhất cho mỗi bản ghi đích (append-only; rút lại bằng retraction)."""
    links = {}
    for r in allrecs:
        if r["kind"] == "variant_link":
            links[r["target"]] = r
    return links


PROTOCOL_TOKENS = {"wifi": ["wifi", "wi fi"], "ble": ["ble", "bluetooth"], "ble_mesh": ["ble", "bluetooth", "mesh"], "zigbee": ["zigbee"],
                   "rf": ["rf", "433mhz"]}


def variant_profiles(allrecs):
    """Hồ sơ model mới nhất cho mỗi (product_id, variant_key)."""
    out = {}
    for r in allrecs:
        if r["kind"] == "variant_profile":
            out[(r["product_id"], r["variant_key"])] = r
    return out


def display_model(profile, variant):
    if not profile:
        return f"{variant or 'chưa xác định model'} (chưa có hồ sơ model)"
    s = f"{profile['brand']} · {profile['model_name']} · {PROTOCOL_LABEL.get(profile['protocol'], profile['protocol'])}"
    if profile.get("app_ecosystem"):
        s += f" · app {profile['app_ecosystem']}"
    if profile.get("is_group"):
        s += " · NHÓM NHIỀU MẪU"
    return s


PROTOCOL_LABEL = {"wifi": "WiFi", "ble": "Bluetooth (BLE)", "ble_mesh": "Bluetooth Mesh", "zigbee": "Zigbee", "rf": "RF",
                  "wifi_zigbee": "WiFi hoặc Zigbee (nhiều bản)", "mixed": "nhiều giao thức", "unknown": "chưa rõ giao thức"}


def record_variant(r, links):
    v = r.get("variant_key") or (r.get("variant") if r["kind"] in ("max_cost", "profit") else None)
    if v:
        return v
    link = links.get(r["id"])
    return link["variant_key"] if link else None


def is_model_specific(r):
    return r["kind"] in MODEL_KINDS or (r["kind"] == "observation" and r.get("metric") in MODEL_METRICS)


def parse_sold(label):
    """'16 Đã bán' -> 16, '1,2k' -> 1200, '2.600+' -> 2600. Không đọc được -> None."""
    if isinstance(label, bool):
        return None
    if isinstance(label, (int, float)):
        return int(label)
    if not isinstance(label, str):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*([kK])?", label)
    if not m:
        return None
    num, k = m.group(1), m.group(2)
    if k:
        return int(float(num.replace(",", ".")) * 1000)
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", num):
        return int(re.sub(r"\D", "", num))
    return int(float(num.replace(",", ".")))


def sold_label_info(label):
    """Đọc nhãn 'đã bán' kèm độ chính xác. exact: số đếm cụ thể ('552 sold', '16 Đã bán', '1.234').
    rounded: nhãn làm tròn hoặc chặn dưới ('2k+', '1,2k', '2.600+', '10k+') -> value là mốc dưới, không dùng để tính tốc độ."""
    n = parse_sold(label)
    if n is None:
        return {"value": None, "precision": "unknown", "label": label}
    if isinstance(label, (int, float)) and not isinstance(label, bool):
        return {"value": n, "precision": "exact", "label": label}
    rounded = bool(re.search(r"\d\s*[kK]|\d\s*\+|tr\b|triệu", label))
    return {"value": n, "precision": "rounded" if rounded else "exact", "label": label}


def listing_key(r):
    """Khóa nhận diện MỘT tin đăng, không phụ thuộc dạng URL: Shopee 'shopee:<shop>.<item>' (cả dạng /product/<shop>/<item> và
    '-i.<shop>.<item>'), Lazada 'lazada:<itemId>' (pdp-i<itemId>), Tiki 'tiki:<id>', còn lại sàn + URL bỏ query/fragment."""
    url = (r.get("url") or "").strip()
    plat = platform_of(r) or ""
    m = re.search(r"shopee\.vn/product/(\d+)/(\d+)", url) or re.search(r"shopee\.vn/.*-i\.(\d+)\.(\d+)", url)
    if m:
        return f"shopee:{m.group(1)}.{m.group(2)}"
    m = re.search(r"lazada\.vn/products/.*?(?:pdp-)?i(\d+)(?:-s\d+)?\.html", url)
    if m:
        return f"lazada:{m.group(1)}"
    m = re.search(r"tiki\.vn/.*?p(\d+)\.html", url)
    if m:
        return f"tiki:{m.group(1)}"
    return f"{plat}:{re.sub(r'[?#].*$', '', url).rstrip('/').lower()}"


def listing_sku_scope(r, links=None, ccfg=None):
    """exact = tin chỉ bán đúng biến thể; multi_sku = tin nhiều phân loại (giá thẻ là phân loại rẻ nhất, nhãn bán gộp).
    Ưu tiên: trường sku_scope trên listing > variant_link (sku_scope) > mẫu tiêu đề trong config/competitor_analysis.json."""
    if r.get("sku_scope") in ("exact", "multi_sku"):
        return r["sku_scope"]
    link = (links or {}).get(r["id"])
    if link and link.get("sku_scope") in ("exact", "multi_sku"):
        return link["sku_scope"]
    ccfg = ccfg or cfg("competitor_analysis")
    t = norm_text(r.get("title") or "")
    if any(re.search(p, t) for p in ccfg.get("multi_sku_title_patterns", [])):
        return "multi_sku"
    return "exact"


def latest_by_listing(recs):
    """Bản mới nhất của mỗi tin đăng (theo listing_key), giữ thứ tự xuất hiện."""
    out = {}
    for r in recs:
        if r["kind"] == "listing":
            out[listing_key(r)] = r
    return out


def _step(value, table, ge=True):
    for th, out in table:
        if (value >= th) if ge else (value <= th):
            return out
    return table[-1][1]


def _group(score, reason, evidence=(), conf=0.0, basis="observed", scope="model", sources=(), **extra):
    return {"score": None if score is None else int(round(score)), "status": "no_data" if score is None else "scored",
            "reason": reason, "evidence_ids": list(dict.fromkeys(evidence)), "sources": sorted({s for s in sources if s}),
            "confidence": round(conf, 3), "basis": basis, "scope": scope, **extra}


def _gate(status, reason, **extra):
    return {"status": status, "reason": reason, **extra}


def _quality(recs, cfgc, now):
    qs = sorted((evidence_quality(r, cfgc, now) for r in recs), reverse=True)[:3]
    return sum(qs) / len(qs) if qs else 0.0


def _vnd(x):
    return f"{int(round(x)):,}".replace(",", ".") + "đ"


def evaluate(store: Store, pid, variant, judgments=None, now=None):
    """Đánh giá một model (product_id + variant_key) theo 5 nhóm và 5 Risk Gate. Chỉ đọc kho, không thu thập thêm.
    Nhóm thiếu dữ liệu -> score=None (không phải 0). Xem config/scoring_weights_v2.json, docs/AGENT_07_AUDIT_DESIGN.md mục 7."""
    w, cfgc = cfg("scoring_weights_v2"), cfg("confidence")
    now = now or dt.datetime.now(dt.timezone.utc)
    prod = store.product(pid)
    allrecs = store.records()
    links = variant_links(allrecs)
    probs = store.problems_for(pid, allrecs)
    own = [norm_text(x) for x in w.get("own_shop_names", [])]
    ident = {"match": [], "product_scope": [], "other_model": [], "unassigned": [], "own_shop": []}
    used, used_links = [], []
    cands = [r for r in allrecs if r.get("product_id") == pid and r["kind"] not in SKIP_KINDS]
    cands += [r for r in allrecs if r.get("problem_ref") in probs and r["kind"] in ("demand", "observation") and r.get("product_id") != pid]
    for r in cands:
        if is_model_specific(r):
            v = record_variant(r, links)
            if variant is None or v is None:
                ident["unassigned"].append(r["id"]); continue
            if v != variant:
                ident["other_model"].append(r["id"]); continue
            if r["kind"] == "listing" and any(o and o in norm_text(r.get("shop_name") or "") for o in own):
                ident["own_shop"].append(r["id"]); continue
            ident["match"].append(r["id"]); used.append(r)
            if r["id"] in links and not (r.get("variant_key") or r.get("variant")):
                used_links.append(links[r["id"]]["id"])
        elif r.get("variant_key") and r["variant_key"] != variant:
            ident["other_model"].append(r["id"])
        else:
            ident["product_scope"].append(r["id"]); used.append(r)
    by_id = {r["id"]: r for r in allrecs}
    ccfg = cfg("competitor_analysis")
    # Cùng tin đăng thu nhiều lần (kể cả URL khác dạng): giá/cạnh tranh lấy lần mới nhất. L_all = mọi tin của model có giá;
    # L = chỉ tin bán đúng biến thể (sku_scope exact). Tin nhiều phân loại vẫn là shop đối thủ nhưng giá thẻ (phân loại rẻ nhất)
    # và nhãn 'đã bán' (gộp mọi phân loại) không dùng cho riêng model này.
    L_all = [r for r in latest_by_listing(used).values() if isinstance(r.get("price_vnd"), (int, float)) and r["price_vnd"] > 0]
    L = [r for r in L_all if listing_sku_scope(r, links, ccfg) == "exact"]
    L_multi = [r for r in L_all if r not in L]
    groups, gates, risks, actions = {}, {}, [], []

    # ---- Nhu cầu: Presence (có mặt, có người bán, có nhãn bán lũy kế) tách khỏi Velocity (bán được bao nhiêu trong một khoảng thời gian)
    dc = w["demand"]
    series = {}
    for r in used:
        if r["kind"] != "listing" or listing_sku_scope(r, links, ccfg) != "exact":
            continue
        info = sold_label_info(r.get("sold_label"))
        if info["value"] is None:
            continue
        day = (r.get("observed_at") or r["collected_at"])[:10]
        # khóa = sàn + mã tin: chỉ so cùng một tin đăng, không bao giờ so 2 tin khác nhau
        series.setdefault(listing_key(r), {})[day] = (info["value"], r["id"], platform_of(r), info["precision"])
    pairs, resets, imprecise = [], [], []
    for key, pts in series.items():
        days = sorted(pts)
        if len(days) < 2:
            continue
        d0, d1 = days[0], days[-1]
        span = (dt.date.fromisoformat(d1) - dt.date.fromisoformat(d0)).days
        if span < dc["velocity_min_days"]:
            continue
        if pts[d0][3] != "exact" or pts[d1][3] != "exact":
            imprecise.append(key); continue  # nhãn làm tròn ('2k+'): không suy ra số bán trong kỳ
        delta = pts[d1][0] - pts[d0][0]
        if delta < 0:
            resets.append(key); continue  # nhãn bị đặt lại hoặc đổi tin: không suy ra tốc độ
        pairs.append({"listing": key, "from": d0, "to": d1, "days": span, "sold_delta": delta,
                      "per_30d": round(delta * 30 / span, 1), "ids": [pts[d0][1], pts[d1][1]], "platform": pts[d1][2]})
    latest_sold = {key: pts[max(pts)][0] for key, pts in series.items()}
    rounded_now = sum(1 for pts in series.values() if pts[max(pts)][3] != "exact")
    cum_total = sum(latest_sold.values())
    shops = {norm_text(r.get("shop_name") or "") or listing_key(r) for r in L_all}
    plats_p = {platform_of(r) for r in L_all}
    dem_recs = [r for r in used if r["kind"] == "demand" and r["type"] in cfgc["criterion_evidence"]["demand"]["demand_grade_types"]]
    attn = [r for r in used if r["kind"] == "observation" and r.get("metric") in TREND_ATTENTION]
    presence = {"listings": len(L_all), "listings_exact_variant": len(L), "listings_multi_sku": len(L_multi), "shops": len(shops),
                "platforms": sorted(plats_p), "listings_with_sold_label": len(latest_sold), "sold_labels_rounded": rounded_now,
                "cumulative_sold_label_total": cum_total, "attention_signals": len(attn), "demand_records": len(dem_recs)}
    velocity = None
    if pairs:
        monthly = round(sum(p["per_30d"] for p in pairs), 1)
        velocity = {"listings_measured": len(pairs), "units_per_30d": monthly, "pairs": pairs,
                    "window": f"{min(p['from'] for p in pairs)} → {max(p['to'] for p in pairs)}"}
    if velocity:
        vs = _step(velocity["units_per_30d"], dc["velocity_score"])
        conf = _quality([by_id[i] for p in pairs for i in p["ids"]], cfgc, now) * min(1, len(pairs) / dc["velocity_min_listings"])
        groups["demand"] = _group(vs, f"Demand Velocity: {len(pairs)} tin đăng đo lại sau {min(p['days'] for p in pairs)}–{max(p['days'] for p in pairs)} ngày, "
                                  f"tăng tổng cộng ≈ {velocity['units_per_30d']} sp/30 ngày (chênh lệch nhãn 'đã bán' giữa 2 lần quan sát, chỉ là mẫu các tin đã đo). "
                                  f"Presence: {len(L_all)} tin, {len(shops)} shop, {len(plats_p)} sàn.",
                                  [i for p in pairs for i in p["ids"]] + [r["id"] for r in dem_recs], conf, "observed",
                                  sources=[p["platform"] for p in pairs], presence=presence, velocity=velocity, level="velocity")
        if velocity["units_per_30d"] >= dc["gate_min_units_30d"]:
            gates["demand"] = _gate("pass", f"Velocity ≈ {velocity['units_per_30d']} sp/30 ngày trên {len(pairs)} tin đăng")
        elif velocity["units_per_30d"] < dc["gate_fail_units_30d"] and len(pairs) >= dc["velocity_min_listings"]:
            gates["demand"] = _gate("fail", f"Velocity rất thấp (≈ {velocity['units_per_30d']} sp/30 ngày) trên {len(pairs)} tin đăng đã đo lại")
        else:
            gates["demand"] = _gate("insufficient", f"Velocity ≈ {velocity['units_per_30d']} sp/30 ngày trên {len(pairs)} tin: chưa đủ mẫu hoặc dưới ngưỡng "
                                    f"{dc['gate_min_units_30d']}")
    else:
        if L_all or dem_recs or attn:
            s = dc["presence_base"]
            s += min(dc["presence_per_shop_max"], dc["presence_per_shop"] * len(shops))
            s += _step(cum_total, dc["presence_sold_bonus"]) if latest_sold else 0
            s += dc["presence_multi_platform_bonus"] if len(plats_p) >= 2 else 0
            if not L_all:
                s = dc["attention_scores"]["high" if any(isinstance(r.get("value"), (int, float)) and r["value"] >= dc["attention_high"] for r in attn) else "low"]
            s = min(s, dc["presence_cap"])
            ev = L_all + dem_recs + attn
            conf = _quality(ev, cfgc, now) * dc["presence_conf_factor"] * min(1, max(1, len(plats_p)) / 2)
            why = (f"Chỉ có Demand Presence (chưa đo được tốc độ bán): {len(L_all)} tin đăng ({len(L)} tin đúng biến thể, {len(L_multi)} tin nhiều phân loại), "
                   f"{len(shops)} shop, {len(plats_p)} sàn"
                   + (f", nhãn 'đã bán' lũy kế {cum_total} trên {len(latest_sold)} tin đúng biến thể" + (f" ({rounded_now} nhãn làm tròn, chỉ là mốc dưới)" if rounded_now else "")
                      if latest_sold else ", không tin đúng biến thể nào có nhãn 'đã bán'")
                   + (f", {len(attn)} tín hiệu chú ý (Trends/CSI, {sum(1 for r in attn if r.get('variant_key'))} gắn model)" if attn else "")
                   + (f", {len(dem_recs)} bản ghi nhu cầu ({sum(1 for r in dem_recs if r.get('variant_key'))} từ đánh giá của đúng model)" if dem_recs else "") + f". Nhãn lũy kế không cho biết bán trong bao lâu; điểm tối đa {dc['presence_cap']}.")
            groups["demand"] = _group(s, why, [r["id"] for r in ev], conf, "observed", scope="model" if L_all else "product",
                                      sources=[platform_of(r) for r in ev], cap=dc["presence_cap"], presence=presence, velocity=None, level="presence")
        else:
            groups["demand"] = _group(None, "Không có bằng chứng nhu cầu nào cho model này.", presence=presence, velocity=None, level="none")
        gates["demand"] = _gate("insufficient", "Chỉ có Demand Presence, chưa có Demand Velocity (cần đo lại nhãn 'đã bán' của cùng tin đăng sau "
                                f"≥ {dc['velocity_min_days']} ngày)" if (L_all or attn) else "Không có dữ liệu nhu cầu")
    if gates["demand"]["status"] != "pass":
        if L_all:
            actions.append({"issue": "Chưa đo được tốc độ bán", "action": f"Đo lại nhãn 'đã bán' của chính các tin đăng này sau ≥ {dc['velocity_min_days']} ngày "
                            "(cùng URL) và thu thêm Shopee/TikTok Shop", "owner": "Agent 03 (thread có PC)"})
        else:
            actions.append({"issue": "Chưa có tin đăng nào của model này", "action": "Xác định model cụ thể rồi thu thập tin đăng của model đó (Shopee/TikTok Shop/Lazada, "
                            f"có nhãn 'đã bán'), sau đó đo lại cùng URL sau ≥ {dc['velocity_min_days']} ngày", "owner": "Agent 03 / Agent 06 (thread có PC)"})
    if imprecise:
        risks.append(f"{len(imprecise)} tin đăng có nhãn 'đã bán' làm tròn (ví dụ '2k+') ở một lần đo: không tính tốc độ từ các tin này")
    if resets:
        risks.append(f"{len(resets)} tin đăng có nhãn 'đã bán' giảm giữa 2 lần đo (đổi tin hoặc đặt lại), không dùng để tính tốc độ")

    # ---- Xu hướng: ưu tiên xu hướng của đúng model; xu hướng nhóm sản phẩm chỉ là bằng chứng gián tiếp
    tc = w["trend"]
    trs_v = [r for r in used if r["kind"] == "trend" and r.get("variant_key") == variant and variant]
    trs_p = [r for r in used if r["kind"] == "trend" and not r.get("variant_key")]
    if not (trs_v or trs_p):
        groups["trend"] = _group(None, "Chưa có bản ghi xu hướng (Agent 02).", scope="product", level="none")
    else:
        t = (trs_v or trs_p)[-1]
        lvl = "variant" if trs_v else "product"
        ev = [by_id[e] for e in t.get("evidence", []) if e in by_id]
        pre = ("Xu hướng của đúng model" if lvl == "variant" else
               f"Bằng chứng gián tiếp: xu hướng của nhóm sản phẩm '{prod['canonical_name']}', chưa xác nhận riêng cho model này")
        if t["stage"] == "unknown":
            groups["trend"] = _group(None, f"{pre}. Agent 02 kết luận UNKNOWN, giữ nguyên: {t['basis']}", [t["id"]], 0.0, "observed",
                                     scope=lvl if lvl == "product" else "model", stage="unknown", level=lvl)
        else:
            nsrc = len({platform_of(r) for r in ev})
            conf = _quality(ev, cfgc, now) * min(1, nsrc / 2) * (1 if lvl == "variant" else tc["indirect_conf_factor"])
            groups["trend"] = _group(tc["stage_score"][t["stage"]], f"{pre}: {t['stage']} ({t['basis']}).",
                                     [t["id"], *[r["id"] for r in ev]], conf, "observed" if lvl == "variant" else "indirect",
                                     scope="model" if lvl == "variant" else "product", sources=[platform_of(r) for r in ev],
                                     stage=t["stage"], level=lvl)
    if groups["trend"]["score"] is None or groups["trend"].get("level") == "product":
        actions.append({"issue": "Xu hướng chưa xác nhận cho model", "action": "Đo xu hướng bằng từ khóa riêng của model (tên hãng + model) ở ≥ 2 nguồn",
                        "owner": "Agent 02"})

    # ---- Cạnh tranh
    cc = w["competition"]
    if len(L) < cc["min_listings"]:
        groups["competition"] = _group(None, f"Mới có {len(L)} tin đăng bán đúng biến thể có giá (cần ≥ {cc['min_listings']})"
                                       + (f"; {len(L_multi)} tin nhiều phân loại không dùng được cho giá." if L_multi else "."))
    else:
        prices = sorted(float(r["price_vnd"]) for r in L)
        med, p25, p75, mn = _quantile(prices, .5), _quantile(prices, .25), _quantile(prices, .75), prices[0]
        shops = {norm_text(r.get("shop_name") or "") or listing_key(r) for r in L_all}
        comp, notes = {}, []
        comp["shop_count"] = _step(len(shops), cc["shop_count"], ge=False)
        notes.append(f"{len(shops)} shop bán model này" + (f" (gồm {len(L_multi)} tin nhiều phân loại; giá chỉ tính {len(L)} tin đúng biến thể)" if L_multi else ""))
        spread = (p75 - p25) / med if med else 0
        if mn < cc["undercut_ratio"] * med:
            comp["price"] = cc["price_scores"]["undercut"]; notes.append(f"có shop bán {_vnd(mn)}, dưới {int(cc['undercut_ratio']*100)}% trung vị {_vnd(med)}")
        elif spread < cc["fixed_price_spread"]:
            comp["price"] = cc["price_scores"]["fixed"]; notes.append(f"giá gần như cố định quanh {_vnd(med)}: không cạnh tranh được bằng giá, lãi phụ thuộc chiết khấu đại lý")
        else:
            comp["price"] = cc["price_scores"]["normal"]; notes.append(f"giá P25–P75 {_vnd(p25)}–{_vnd(p75)}")
        sold = [parse_sold(r.get("sold_label")) or 0 for r in L]
        if sum(1 for x in sold if x > 0) >= 2:  # cần ≥ 2 tin có nhãn bán mới nói được ai dẫn đầu
            share = max(sold) / sum(sold)
            comp["leader"] = _step(share, cc["leader_share"]); notes.append(f"tin dẫn đầu chiếm {share:.0%} số đã bán")
        brand = variant.split("-")[0] if variant else ""
        pats = [norm_text(p) for p in cc["incumbent_patterns"] + cc["brand_aliases"].get(brand, [])]
        inc = [r for r in L_all if any(p and p in norm_text(r.get("shop_name") or "") for p in pats)]
        ishare = len(inc) / len(L_all)
        comp["incumbent"] = _step(ishare, cc["incumbent_share"]); notes.append(f"{len(inc)}/{len(L_all)} tin của shop hãng/nhà phân phối/official")
        diff_ev = [r for r in L if r.get("weaknesses")] + [r for r in used if r["kind"] == "demand" and r["type"] in ("complaint", "unmet_need")]
        if diff_ev:
            comp["differentiation"] = cc["differentiation_score"]; notes.append(f"{len(diff_ev)} bằng chứng điểm yếu đối thủ / nhu cầu chưa đáp ứng")
        s = sum(comp.values()) / len(comp)
        capped = None
        if not diff_ev:
            if s > cc["cap_without_differentiation"]:
                capped = cc["cap_without_differentiation"]; s = capped
            notes.append("chưa có bằng chứng khả năng khác biệt (cần Agent 04)")
        plats_c = {platform_of(r) for r in L}
        band = next(lbl for th, lbl in cc["bands"] if s >= th)
        groups["competition"] = _group(s, f"{band}: " + "; ".join(notes) + f". Mẫu từ trang tìm kiếm {', '.join(sorted(plats_c))}"
                                       + (", chưa có Shopee." if "shopee" not in plats_c else "."),
                                       [r["id"] for r in L + diff_ev], _quality(L, cfgc, now) * min(1, len(plats_c) / 2), "observed",
                                       sources=plats_c, band=band, components=comp, cap=capped,
                                       price={"min": round(mn), "p25": round(p25), "median": round(med), "p75": round(p75), "max": round(prices[-1])})
        if not diff_ev:
            actions.append({"issue": "Chưa có bằng chứng khác biệt", "action": "Phân tích điểm yếu đối thủ, đánh giá khách chê gì ở model này",
                            "owner": "Agent 04"})
    if groups["competition"]["score"] is None:
        actions.append({"issue": "Thiếu dữ liệu cạnh tranh", "action": "Thu thêm tin đăng có giá của đúng model (≥ 3), ưu tiên Shopee", "owner": "Agent 06"})

    # ---- Nhà cung cấp
    sc = w["supplier"]
    is_vn_sup = lambda r: r["kind"] == "supplier" and r.get("market", "VN") == "VN" and r.get("price_basis") != "source_reference"
    sups = [r for r in used if is_vn_sup(r)]
    unassigned_sup = [i for i in ident["unassigned"] if i in by_id and is_vn_sup(by_id[i])]
    latest_rv, vendor_of = {}, {}
    for r in allrecs:
        if r["kind"] == "vendor_review":
            latest_rv[r["vendor_key"]] = r
            for sid in r.get("supplier_refs") or []:
                vendor_of[sid] = r["vendor_key"]  # đánh giá NCC là đánh giá pháp nhân: dùng bản mới nhất của NCC đó, kể cả khi bản mới gắn tin khác
    dom_vendor = {}
    for vk, r in latest_rv.items():
        for d_ in [vk, *(r.get("vendor_domains") or [])]:
            dom_vendor[d_.lower().removeprefix("www.")] = vk
    def vendor_for(s):
        # Bản ghi NCC mới (ví dụ trang đúng model) chưa nằm trong supplier_refs: nhận theo tên miền trang của pháp nhân đã đánh giá
        if s["id"] in vendor_of:
            return vendor_of[s["id"]]
        d_ = (domain(s.get("url")) or "").lower().removeprefix("www.")
        return dom_vendor.get(d_)
    rows = []
    for s in sups:
        rv = latest_rv.get(vendor_for(s))
        rows.append((s, rv, vendor_assessment(rv) if rv else None))
    assessed = [x for x in rows if x[2]]
    cost_sups = [s for s in sups if s.get("price_basis") in COST_BASES and s.get("price_tiers")]
    if not sups:
        groups["supplier"] = _group(None, "Chưa có nhà cung cấp trong nước gắn đúng model này.")
        gates["supplier"] = _gate("insufficient", "Chưa có NCC trong nước cho đúng model")
        actions.append({"issue": "Chưa có NCC cho model", "action": "Tìm NCC trong nước bán đúng model, hoặc gán model cho NCC đã có (variant_link)", "owner": "Agent 05"})
    elif not assessed:
        groups["supplier"] = _group(None, f"Có {len(sups)} NCC nhưng chưa có đánh giá 6 tiêu chí.", [s["id"] for s in sups])
        gates["supplier"] = _gate("insufficient", "NCC chưa được đánh giá 6 tiêu chí")
        actions.append({"issue": "NCC chưa đánh giá", "action": "Đánh giá 6 tiêu chí cho NCC của model", "owner": "Agent 05"})
    else:
        s, rv, a = max(assessed, key=lambda x: x[2]["score"])
        bonus = sc["dealer_quote_bonus"] if any(x.get("price_basis") == "dealer_quote" for x in cost_sups) else (
            sc["wholesale_listed_bonus"] if cost_sups else 0)
        why = (f"NCC tốt nhất cho model: {a['vendor_name']} {a['score']}/12 (mức {a['level']}). "
               + ("Có giá sỉ/báo giá đại lý." if cost_sups else "Chưa có báo giá sỉ/đại lý xác minh: không đánh giá giá nhập là tốt; giá bán lẻ không phải giá vốn."))
        groups["supplier"] = _group(a["score"] / 12 * sc["vendor_points_scale"] + bonus, why,
                                    [s["id"], rv["id"], *[c["id"] for c in cost_sups]], a["score"] / 12,
                                    "observed", sources=[domain(s.get("url"))], vendors=[x[2] for x in assessed])
        warranty_missing = rv["criteria"]["warranty"]["status"] == "missing"
        probs_s = []
        if a["level"] == "low":
            probs_s.append(f"mức NCC thấp ({a['score']}/12)")
        if not a["legal_entity_confirmed"]:
            probs_s.append("pháp nhân chưa đối chiếu ở nguồn độc lập")
        if warranty_missing:
            probs_s.append("chưa có chính sách bảo hành")
        gates["supplier"] = _gate("insufficient" if probs_s else "pass", "; ".join(probs_s) if probs_s else
                                  f"{a['vendor_name']}: mức {a['level']}, pháp nhân đã đối chiếu, có bảo hành")
        if a["ask_vendor"]:
            risks.append(f"NCC {a['vendor_name']} chưa rõ: {', '.join(a['ask_vendor'])}")
            actions.append({"issue": f"Thiếu thông tin NCC {a['vendor_name']}", "action": "Hỏi NCC: " + ", ".join(a["ask_vendor"]), "owner": "Anh Khoa"})
        if a["only_self_stated"]:
            risks.append(f"NCC {a['vendor_name']} mới tự công bố: {', '.join(a['only_self_stated'])}")
        if probs_s:
            risks.append("Rủi ro nguồn cung: " + "; ".join(probs_s))
    if unassigned_sup and not sups:
        risks.append(f"Có NCC chưa xác định bán model nào: {', '.join(unassigned_sup)}")

    # ---- Tài chính: Gate chỉ đạt khi giá vốn đã xác minh (báo giá đại lý / anh cung cấp) VÀ đủ chi phí shop có nguồn
    fc = w["financial"]
    tgt = fc["target_margin_pct"]
    amap = {a["id"]: a for a in store.assumptions()}
    mcs = [r for r in used if r["kind"] == "max_cost" and r.get("status") in ("partial", "complete")]
    mc = mcs[-1] if mcs else None
    ceil10 = (mc["scenarios"]["base"].get(f"max_unit_cost_at_{tgt}pct") if mc else None)
    excl = (mc or {}).get("excluded_costs") or []
    tier_price = lambda s: min(s["price_tiers"], key=lambda x: x.get("min_qty", 1))["price"]
    is_verified_cost = lambda s: s.get("price_basis") == "dealer_quote" and s.get("verification") in ("user_input", "observed")
    vsups = [s for s in cost_sups if is_verified_cost(s)]
    real_cost = min(((tier_price(s), s) for s in vsups), key=lambda x: x[0]) if vsups else None
    unverified = [s for s in cost_sups if not is_verified_cost(s)]
    listed_cost = min(((tier_price(s), s) for s in unverified), key=lambda x: x[0]) if unverified else None

    def profit_issues(p):
        """Vì sao một mô phỏng lợi nhuận 'complete' vẫn chưa đủ để Gate Tài chính đạt."""
        issues, inp = [], p.get("inputs") or {}
        ref = (inp.get("unit_cost") or {}).get("ref") or ""
        if not ref.startswith("user:"):
            ids = ref.split("+") if ref else []
            if not ids or not all(by_id.get(i, {}).get("kind") == "supplier" and record_variant(by_id[i], links) == variant for i in ids):
                issues.append("giá nhập không tham chiếu NCC của đúng model")
            elif not all(is_verified_cost(by_id[i]) for i in ids):
                issues.append("giá nhập là giá sỉ niêm yết / chưa phải báo giá đại lý đã xác minh")
        for f in COST_PROFILE_FIELDS:
            r_ = (inp.get(f) or {}).get("ref") or ""
            if r_.startswith("assumption:"):
                for aid in r_.split(":", 1)[1].split("+"):
                    a = amap.get(aid)
                    if a is None or a.get("status") == "missing" or a.get("verification") in ("estimated", "inferred"):
                        issues.append(f"chi phí {f} là ước tính hoặc thiếu nguồn ({aid})")
        if p.get("unreferenced"):
            issues.append("đầu vào không có nguồn: " + ", ".join(p["unreferenced"]))
        return issues

    profits = [r for r in used if r["kind"] == "profit" and r.get("status") == "complete"]
    checked = [(p, profit_issues(p)) for p in profits]
    good = [p for p, iss in checked if not iss]
    if good or checked:
        p, iss = (good[-1], []) if good else checked[-1]
        m = p["scenarios"]["base"]["net_margin_pct"]
        groups["financial"] = _group(_step(m, fc["margin_score"]) if not iss else min(fc["known_cost_cap"], _step(m, fc["margin_score"])),
                                     f"Mô phỏng đủ chi phí: lãi ròng kịch bản cơ sở {m}%."
                                     + (" Giá vốn và chi phí đều có nguồn đã xác minh." if not iss else " Chưa được dùng để kết luận vì: " + "; ".join(iss) + "."),
                                     [p["id"]], p.get("input_confidence", 0.0) * (1 if not iss else cfgc["verification_factor"]["estimated"]),
                                     "observed" if not iss else "estimated", sources=["profit_simulation"], net_margin_pct=m, issues=iss)
        if m < 0 and not iss:
            gates["financial"] = _gate("fail", f"Lãi ròng âm ({m}%) với đủ chi phí và giá vốn đã xác minh")
        elif iss:
            gates["financial"] = _gate("insufficient", "; ".join(iss))
        else:
            gates["financial"] = (_gate("pass", f"Lãi ròng {m}% ≥ mục tiêu {tgt}% (giá vốn đã xác minh, đủ chi phí)") if m >= tgt else
                                  _gate("insufficient", f"Lãi ròng {m}% dưới mục tiêu {tgt}%: cần đàm phán giá nhập"))
    elif (real_cost or listed_cost) and ceil10:
        c, s = real_cost or listed_cost
        h = 1 - c / ceil10
        sc_ = 5 if h < 0 else 30 if h < 0.1 else 45 if h < 0.2 else fc["known_cost_cap"]
        full = mc.get("status") == "complete"
        groups["financial"] = _group(sc_, f"Giá nhập {_vnd(c)} ({s['price_basis']}, {s['id']}) so với trần {_vnd(ceil10)} (lãi {tgt}%"
                                     + (", đã trừ đủ chi phí shop" if full else f", chưa trừ {len(excl)} khoản chi phí shop") + f"): còn {h:.0%}. "
                                     + ("" if real_cost else "Giá sỉ niêm yết, chưa phải báo giá xác minh. ") + "Đây là ước tính có điều kiện.",
                                     [mc["id"], s["id"]], cfgc["verification_factor"]["estimated"] * freshness(mc, "price", cfgc, now),
                                     "estimated", sources=["max_cost"], cap=fc["known_cost_cap"], ceiling=ceil10)
        if real_cost and c > ceil10:
            gates["financial"] = _gate("fail", f"Giá nhập đã xác minh {_vnd(c)} vượt trần {_vnd(ceil10)} để lãi {tgt}%")
        elif real_cost and full:
            gates["financial"] = _gate("pass", f"Giá nhập đã xác minh {_vnd(c)} ≤ trần {_vnd(ceil10)} đã trừ đủ chi phí shop")
        else:
            gates["financial"] = _gate("insufficient", ("Có báo giá nhưng trần chưa trừ đủ chi phí shop" if real_cost else
                                                        "Chỉ có giá sỉ niêm yết, chưa có báo giá đại lý đã xác minh"))
    elif mc and ceil10:
        dn = mc.get("discount_needed") or {}
        rsup = by_id.get(dn.get("ref"))
        d = (dn.get("base") or {}).get(f"min_discount_pct_at_{tgt}pct")
        if d is not None and rsup and record_variant(rsup, links) == variant:
            groups["financial"] = _group(min(fc["ceiling_cap"], _step(d, fc["discount_score"], ge=False)),
                                         f"Chưa có giá nhập thực tế (giá lẻ đề xuất không phải giá nhập). Trần giá nhập {_vnd(ceil10)} (lãi {tgt}%, chưa trừ {len(excl)} khoản chi phí shop) "
                                         f"= cần chiết khấu ≥ {d}% trên giá lẻ đề xuất {_vnd(dn['retail_price'])}. Đánh giá có điều kiện, tối đa {fc['ceiling_cap']} điểm.",
                                         [mc["id"], rsup["id"]], cfgc["verification_factor"]["estimated"] * freshness(mc, "price", cfgc, now),
                                         "estimated", sources=["max_cost"], cap=fc["ceiling_cap"], ceiling=ceil10, discount_needed_pct=d)
        else:
            groups["financial"] = _group(None, f"Chỉ có trần giá nhập {_vnd(ceil10)} (lãi {tgt}%), chưa có giá lẻ đề xuất cùng model hay báo giá để so.",
                                         [mc["id"]], ceiling=ceil10)
        gates["financial"] = _gate("insufficient", "Chưa có giá nhập thực tế đã xác minh: không khẳng định có lợi nhuận")
    else:
        groups["financial"] = _group(None, "Chưa có giá bán thị trường / trần giá nhập cho model này (Agent 06).")
        gates["financial"] = _gate("insufficient", "Chưa có dữ liệu tài chính cho model")
    if not real_cost and not good:
        actions.append({"issue": "Chưa có giá nhập đã xác minh", "action": "Xin báo giá đại lý cho đúng model, ghi supplier price_basis=dealer_quote kèm variant_key",
                        "owner": "Anh Khoa"})
    if excl:
        risks.append("Trần giá nhập chưa trừ: " + ", ".join(excl))
        actions.append({"issue": "Thiếu chi phí thực tế", "action": "Cung cấp chi phí shop còn thiếu (" + ", ".join(COST_LABEL.get(x, x) for x in excl) + ") để Agent 06 trừ tiếp",
                        "owner": "Anh Khoa → Agent 06"})

    # ---- Product Identity: hồ sơ model (thương hiệu, model, giao thức) + kiểm tra tin đăng không lệch giao thức
    ic = w["identity"]
    why_i, warn_i = [], []
    profile = variant_profiles(allrecs).get((pid, variant)) if variant else None
    if variant is None:
        why_i.append("chưa xác định model (variant_key) cho sản phẩm này")
    else:
        if profile is None:
            why_i.append("chưa có hồ sơ model (thương hiệu, tên model, giao thức)")
        elif profile.get("is_group"):
            why_i.append(f"'{variant}' là nhóm nhiều mẫu/nhiều hãng, chưa phải một model: {profile.get('group_note') or profile['model_name']}")
        if len(L) < ic["min_listings"]:
            why_i.append(f"chỉ {len(L)} tin đăng bán đúng biến thể (cần ≥ {ic['min_listings']}"
                         + (f"; {len(L_multi)} tin khác là tin nhiều phân loại, không tách được giá riêng" if L_multi else "") + ")")
        else:
            ps = sorted(float(r["price_vnd"]) for r in L)
            p25, p75 = _quantile(ps, .25), _quantile(ps, .75)
            if p25 > 0 and p75 / p25 > ic["max_p75_p25_ratio"]:
                why_i.append(f"giá trải rộng (P75/P25 = {p75 / p25:.2f} > {ic['max_p75_p25_ratio']}): có thể đang gộp nhiều mẫu")
        if profile and profile["protocol"] in PROTOCOL_TOKENS:
            mine = PROTOCOL_TOKENS[profile["protocol"]]
            others = {t for k, v in PROTOCOL_TOKENS.items() if k != profile["protocol"] for t in v} - set(mine)
            bad = [r["id"] for r in L_all if not any(tok in rec_text(r).split() for tok in mine) and any(tok in rec_text(r).split() for tok in others)]
            if bad:
                why_i.append(f"{len(bad)} tin đăng ghi giao thức khác '{profile['protocol']}': {', '.join(bad)}")
        if profile and len(profile.get("versions") or []) > 1:
            warn_i.append(f"model gộp {len(profile['versions'])} phiên bản: {', '.join(profile['versions'])}")
    if unassigned_sup and not sups:
        why_i.append(f"{len(unassigned_sup)} NCC chưa xác định bán model nào")
    gates["identity"] = _gate("insufficient" if why_i else "pass", "; ".join(why_i) if why_i else
                              f"Hồ sơ model rõ; mọi dữ liệu theo model đều đúng variant_key; loại {len(ident['other_model'])} bản ghi model khác, "
                              f"{len(ident['unassigned'])} bản ghi chưa gán model")
    risks += [f"Nhận diện: {x}" for x in warn_i]
    if why_i:
        actions.append({"issue": "Chưa xác định đúng model", "action": "Chọn một mẫu cụ thể (hãng + model + giao thức), ghi hồ sơ model, gán NCC (variant_link) rồi thu giá lại",
                        "owner": "Agent 06 / Agent 05"})

    # ---- Nhận định AI (chỉ trong khung của công cụ)
    applied = []
    for g, j in (judgments or {}).items():
        if g not in JUDGEABLE:
            raise APHError(f"Chỉ nhận nhận định AI cho {JUDGEABLE}, không cho '{g}'")
        base = groups[g]
        if base["score"] is None:
            raise APHError(f"{g}: nhóm chưa có dữ liệu, không được tự chấm")
        evid = j.get("evidence") or []
        if not evid or any(e not in {r["id"] for r in used} for e in evid):
            raise APHError(f"{g}: evidence phải là id bản ghi đầu vào của đúng model")
        if len((j.get("reason") or "").strip()) < 10:
            raise APHError(f"{g}: cần reason")
        lo, hi = max(0, base["score"] - 10), min(base.get("cap") or 100, base["score"] + 10)
        val = max(lo, min(hi, float(j["score"])))
        groups[g] = {**base, "score": int(round(val)), "tool_score": base["score"], "basis": "ai_judgment",
                     "reason": base["reason"] + f" | Nhận định AI: {j['reason']}", "evidence_ids": list(dict.fromkeys(base["evidence_ids"] + evid))}
        applied.append({"group": g, "requested": j["score"], "applied": int(round(val)), "range": [lo, hi]})

    # ---- Tổng hợp
    gw = {g: w["groups"][g]["weight"] for g in GROUPS}
    scored = [g for g in GROUPS if groups[g]["score"] is not None]
    cov = sum(gw[g] for g in scored)
    total = round(sum(gw[g] * groups[g]["score"] for g in scored) / cov, 1) if cov else None
    dconf = round(100 * sum(gw[g] * groups[g]["confidence"] for g in GROUPS) / sum(gw.values()), 1)
    sr = w["status_rules"]
    gates["data_confidence"] = (_gate("pass", f"Data Confidence {dconf} ≥ {sr['min_data_confidence']}") if dconf >= sr["min_data_confidence"]
                                else _gate("insufficient", f"Data Confidence {dconf} < {sr['min_data_confidence']}: dữ liệu ít, một nguồn hoặc một thời điểm"))
    nulls = [g for g in GROUPS if g not in scored]
    fails = [k for k, v in gates.items() if v["status"] == "fail"]
    insuf = [k for k, v in gates.items() if v["status"] == "insufficient"]
    conditions = []
    if fails:
        status = "NO_GO"; conditions = [f"{k}: {gates[k]['reason']}" for k in fails]
    elif ("demand" in insuf or "identity" in insuf or len(nulls) > sr["max_null_groups"] or "data_confidence" in insuf):
        status = "NEED_MORE_DATA"
        conditions = [f"{k}: {gates[k]['reason']}" for k in insuf] + ([f"{len(nulls)} nhóm chưa có điểm: {', '.join(nulls)}"] if len(nulls) > sr["max_null_groups"] else [])
    elif not insuf and total is not None and total >= sr["go_min_score"] and cov >= sr["go_min_weight_coverage"]:
        status = "GO"
    elif cov >= sr["no_go_low_score_min_coverage"] and total is not None and total < sr["no_go_low_score"]:
        status = "NO_GO"; conditions = [f"Điểm {total} < {sr['no_go_low_score']} khi dữ liệu đã phủ {cov}% trọng số"]
    else:
        status = "CONDITIONAL_GO"
        conditions = [f"{k}: {gates[k]['reason']}" for k in insuf]
        if cov < sr["go_min_weight_coverage"]:
            conditions.append(f"weight_coverage {cov}% < {sr['go_min_weight_coverage']}%: bổ sung {', '.join(nulls)}")
        if total is not None and total < sr["go_min_score"]:
            conditions.append(f"Điểm {total} < {sr['go_min_score']}")
    if status == "GO":
        actions = [{"issue": "Đủ điều kiện", "action": "Đưa vào danh sách thử nghiệm kinh doanh (vốn thử 10 triệu)", "owner": "Anh Khoa"}]
    lbl = lambda g: w["groups"][g]["label"]
    strengths = [f"{lbl(g)} {groups[g]['score']}: {groups[g]['reason']}" for g in scored if groups[g]["score"] >= 70]
    weaknesses = [f"{lbl(g)} {groups[g]['score']}: {groups[g]['reason']}" for g in scored if groups[g]["score"] < 50]
    missing = [f"{lbl(g)}: {groups[g]['reason']}" for g in nulls] + [f"Gate {k}: {gates[k]['reason']}" for k in insuf]
    risks = [f"Gate {k}: {gates[k]['reason']}" for k in fails] + risks
    names = [problem["statement"] for problem in (store.problem(p) for p in probs)]
    stage = groups["trend"].get("stage", "unknown")
    opp = (f"{display_model(profile, variant)} (nhóm sản phẩm: {prod['canonical_name']}). "
           + (f"Giải quyết vấn đề: {'; '.join(names)}. " if names else "Chưa gắn vấn đề khách hàng nào. ")
           + f"Xu hướng cấp sản phẩm: {stage}.")
    snap_ids = list(dict.fromkeys([r["id"] for r in used] + used_links + [x[1]["id"] for x in assessed] + ([profile["id"]] if profile else [])))
    snapshot = [{"id": i, "kind": by_id[i]["kind"], "collected_at": by_id[i]["collected_at"]} for i in snap_ids if i in by_id]
    model = {"display": display_model(profile, variant), "profile_id": profile["id"] if profile else None,
             **({k: profile.get(k) for k in ("brand", "model_name", "protocol", "app_ecosystem", "versions", "is_group", "group_note")} if profile else {})}
    return {"product_id": pid, "variant_key": variant, "product_name": prod["canonical_name"], "model": model, "agent": "opportunity_evaluator",
            "weights_version": w["version"], "opportunity": opp, "groups": groups, "total": total, "weight_coverage": cov,
            "data_confidence": dconf, "gates": gates, "status": status, "suggested_research_status": w["research_status_map"][status],
            "conditions": conditions, "strengths": strengths, "weaknesses": weaknesses, "risks": risks, "missing_data": missing,
            "next_actions": actions, "identity": {k: len(v) for k, v in ident.items()} | {"other_model_ids": ident["other_model"],
                                                                                        "unassigned_ids": ident["unassigned"], "own_shop_ids": ident["own_shop"]},
            "input_snapshot": snapshot, "judgments_applied": applied}


def same_evaluation(old, new):
    """Hai lần đánh giá giống hệt: cùng phiên bản trọng số, cùng tập bản ghi đầu vào, cùng kết quả."""
    key = lambda e: (e.get("weights_version"), sorted(x["id"] for x in e.get("input_snapshot", [])), e.get("status"), e.get("total"),
                     e.get("data_confidence"), json.dumps(e.get("gates"), sort_keys=True, ensure_ascii=False),
                     json.dumps(e.get("next_actions"), sort_keys=True, ensure_ascii=False))
    return key(old) == key(new)


QUESTIONS_03 = [
    ("real_demand", "Có nhu cầu thật không"), ("trend", "Nhu cầu tăng hay giảm"), ("keywords", "Người mua tìm bằng từ khóa nào"),
    ("features", "Người mua quan tâm tính năng nào"), ("buying", "Vì sao người mua mua"), ("non_buy", "Vì sao không mua hoặc trả hàng"),
    ("price", "Mức giá được chấp nhận"), ("seasonality", "Có mùa vụ không"), ("platforms", "Khác biệt giữa các nền tảng"),
]


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def _seasonality(series_obs, dcfg):
    """Mùa vụ từ chuỗi chỉ số tìm kiếm. Cần >= 1 năm và đủ điểm; 1 năm chỉ là chỉ báo (chưa lặp lại)."""
    best, sparse = None, []
    noisy = cfg("noisy_series")["excluded"]
    for r in series_obs:
        if r["id"] in noisy:  # từ khóa nhiễu đã kiểm chứng (config/noisy_series.json)
            sparse.append(r["id"]); continue
        pts = [(p["t"][:10], p["v"]) for p in r.get("series") or [] if isinstance(p.get("v"), (int, float)) and p.get("t")]
        if len(pts) < 2:
            continue
        # chuỗi thưa (đa số tuần = 0) hoặc quá thấp: tháng 'cao' chỉ là nhiễu, không dùng để xét mùa vụ
        if sum(1 for _, v in pts if v > 0) / len(pts) < 0.5 or max(v for _, v in pts) < 10:
            sparse.append(r["id"]); continue
        span = (dt.date.fromisoformat(max(t for t, _ in pts)) - dt.date.fromisoformat(min(t for t, _ in pts))).days
        # ưu tiên chuỗi gắn đúng model, rồi chuỗi dài nhất, rồi độ tin cậy
        rank = (bool(r.get("variant_key")), span, len(pts), r.get("confidence") or 0)
        if best is None or rank > best[0]:
            best = (rank, pts, r)
    if not best:
        return {"status": "unknown", "reason": "Chưa có chuỗi chỉ số tìm kiếm theo thời gian" + (f" đủ dày và không nhiễu ({len(sparse)} chuỗi thưa/nhiễu bị bỏ)." if sparse else "."),
                "evidence": sparse}
    (_, span, *_), pts, r = best
    if span < dcfg["seasonality_min_days"] or len(pts) < dcfg["seasonality_min_points"]:
        return {"status": "unknown", "reason": f"Chuỗi dài nhất chỉ {len(pts)} mốc trong {span} ngày (cần ≥ {dcfg['seasonality_min_points']} mốc, "
                                               f"≥ {dcfg['seasonality_min_days']} ngày).", "evidence": [r["id"]]}
    by_m = {}
    for t, v in pts:
        by_m.setdefault(int(t[5:7]), []).append(v)
    avg = {m: sum(v) / len(v) for m, v in by_m.items()}
    overall = sum(avg.values()) / len(avg)
    if overall <= 0:
        return {"status": "unknown", "reason": "Chỉ số tìm kiếm bằng 0 gần như cả năm.", "evidence": [r["id"]]}
    peaks = sorted(m for m, v in avg.items() if v >= overall * dcfg["seasonality_peak_ratio"])
    years = span / 365
    return {"status": "indicative" if years < 2 else "observed", "peak_months": peaks, "monthly_avg": {str(m): round(v, 1) for m, v in sorted(avg.items())},
            "reason": (f"Tháng cao hơn trung bình ≥ {int((dcfg['seasonality_peak_ratio'] - 1) * 100)}%: {', '.join(map(str, peaks))}" if peaks
                       else "Không có tháng nào nổi bật") + (f". Chỉ {years:.1f} năm dữ liệu: chỉ báo, chưa xác nhận mùa vụ lặp lại (có thể lẫn với xu hướng tăng/giảm trong năm)." if years < 2 else "."),
            "evidence": [r["id"]], "scope": "model" if r.get("variant_key") else "product"}


def demand_analysis(store: Store, pid, variant, now=None):
    """Agent 03 Demand Analyst: tổng hợp nhu cầu của một model từ dữ liệu đã có trong kho. Chỉ đọc, không thu thập, không suy diễn số liệu.
    Tách 4 loại nhu cầu (tìm kiếm, tương tác, bán hàng, đánh giá) và trả lời 9 câu hỏi; câu nào thiếu dữ liệu ghi unknown + dữ liệu cần thu."""
    dcfg = cfg("demand_analysis")
    e = evaluate(store, pid, variant, now=now)
    allrecs = store.records()
    by_id = {r["id"]: r for r in allrecs}
    used = [by_id[x["id"]] for x in e["input_snapshot"] if x["id"] in by_id]
    obs = [r for r in used if r["kind"] == "observation"]
    dem = [r for r in used if r["kind"] == "demand"]
    links = variant_links(allrecs)
    ccfg = cfg("competitor_analysis")
    L_all = list(latest_by_listing(used).values())  # mỗi tin đăng một lần (theo sàn + mã tin)
    L = [r for r in L_all if listing_sku_scope(r, links, ccfg) == "exact"]  # giá/nhãn bán chỉ của tin đúng biến thể
    dg, tg = e["groups"]["demand"], e["groups"]["trend"]
    missing, recs_out = [], []

    # 1) Tìm kiếm
    srch = [r for r in obs if r["metric"] in dcfg["search_metrics"]]
    search = {"signals": [{"id": r["id"], "metric": r["metric"], "platform": r.get("platform"), "value": r.get("value"), "unit": r.get("unit"),
                           "observed_at": r.get("observed_at") or r["collected_at"][:10], "scope": "model" if r.get("variant_key") else "product",
                           "note": (r.get("note") or "")[:200]} for r in srch]}
    if not srch:
        missing.append("Chỉ số tìm kiếm (Google Trends / TikTok Search) theo từ khóa của model")
    # 2) Tương tác (lượt xem/thích): chỉ là tín hiệu chú ý, không phải nhu cầu mua
    eng = [r for r in obs if r["metric"] in dcfg["engagement_metrics"]]
    engagement = {"signals": [{"id": r["id"], "metric": r["metric"], "platform": r.get("platform"), "value": r.get("value"),
                               "observed_at": r.get("observed_at") or r["collected_at"][:10]} for r in eng],
                  "note": "Lượt xem/thích là tín hiệu chú ý, không chứng minh có người mua."}
    # 3) Bán hàng: Presence / Velocity từ Agent 07 (cùng quy tắc)
    # 4) Đánh giá / phản hồi người mua
    def aspect(r):
        return r.get("aspect") or dcfg["aspect_from_type"].get(r["type"], "pain_point")
    themes = {}
    for r in dem:
        k = (aspect(r), norm_text(r["theme"]))
        t = themes.setdefault(k, {"aspect": aspect(r), "theme": r["theme"], "mentions": 0, "sample_size": 0, "records": [], "platforms": set(), "sources": 0})
        t["mentions"] += r.get("mentions") or 1
        t["sample_size"] += r.get("sample_size") or 0
        t["records"].append(r["id"]); t["sources"] += r.get("independent_sources") or 1
        if r.get("platform"):
            t["platforms"].add(r["platform"])
    def theme_list(asp):
        out = []
        for t in themes.values():
            if t["aspect"] not in asp:
                continue
            n, N = t["mentions"], t["sample_size"]
            small = (N and N < dcfg["small_sample_reviews"]) or n < dcfg["min_mentions_for_pattern"]
            out.append({"theme": t["theme"], "aspect": t["aspect"], "mentions": n, "sample_size": N or None,
                        "share": round(n / N, 2) if N else None, "platforms": sorted(t["platforms"]), "evidence": t["records"],
                        "reliability": "mẫu nhỏ, chỉ là tín hiệu, không khái quát" if small else "lặp lại trong mẫu đủ lớn"})
        return sorted(out, key=lambda x: -x["mentions"])
    pain, buy, nonbuy = theme_list({"pain_point"}), theme_list({"buying_reason", "use_case"}), theme_list({"non_buy_reason", "return_reason"})
    feats, questions = theme_list({"feature"}), theme_list({"question"})
    rv = [r for r in L if isinstance(r.get("review_count"), (int, float))]
    review_demand = {"listings_with_reviews": len(rv), "review_count_total": int(sum(r["review_count"] for r in rv)),
                     "ratings": [r["rating"] for r in L if isinstance(r.get("rating"), (int, float))],
                     "themes_recorded": len(themes), "reviews_read": sum(t["sample_size"] for t in themes.values()) or None}
    if not themes:
        missing.append("Nội dung đánh giá/bình luận của người mua (Lazada, Tiki, Shopee, TikTok): điểm đau, lý do mua, lý do trả hàng")

    # Từ khóa
    kw = [r for r in obs if r["metric"] in dcfg["keyword_metrics"]]
    keywords = [{"keyword": r.get("value"), "platform": r.get("platform"), "id": r["id"], "observed_at": r.get("observed_at") or r["collected_at"][:10]} for r in kw]
    if not kw:
        missing.append("Từ khóa người mua dùng (gợi ý tìm kiếm Shopee/TikTok, cụm liên quan Google Trends)")

    # Giá được chấp nhận
    prices = [r["price_vnd"] for r in L if isinstance(r.get("price_vnd"), (int, float)) and r["price_vnd"] > 0]
    sold_prices = [r["price_vnd"] for r in L if isinstance(r.get("price_vnd"), (int, float)) and (parse_sold(r.get("sold_label")) or 0) > 0]
    acc = [r for r in dem if r["type"] == "price_acceptance" and (r.get("price_low_vnd") or r.get("price_high_vnd"))]
    price = {"listing_price": {"n": len(prices), "min": min(prices) if prices else None, "median": _median(prices), "max": max(prices) if prices else None},
             "price_of_listings_with_sales": {"n": len(sold_prices), "min": min(sold_prices) if sold_prices else None,
                                              "median": _median(sold_prices), "max": max(sold_prices) if sold_prices else None},
             "stated_acceptance": [{"low": r.get("price_low_vnd"), "high": r.get("price_high_vnd"), "theme": r["theme"], "id": r["id"]} for r in acc],
             "buyer_comments": theme_list({"price"}),
             "note": "Giá của tin đã có nhãn 'đã bán' cho biết mức giá từng có người mua, không phải giá tối ưu. Nhãn lũy kế không cho biết bán ở giá nào."}
    if len(sold_prices) < 3:
        missing.append("≥ 3 tin đăng có nhãn 'đã bán' để biết mức giá người mua đã chấp nhận")

    # Mùa vụ
    seas = _seasonality([r for r in srch if r.get("series")], dcfg)
    if seas["status"] == "unknown":
        missing.append("Chuỗi Google Trends ≥ 12 tháng theo tuần cho từ khóa của model (để xét mùa vụ)")

    # Nền tảng
    plat = {}
    for r in L:
        p = platform_of(r)
        d = plat.setdefault(p, {"listings": 0, "shops": set(), "prices": [], "sold_label_total": 0, "listings_with_sold": 0})
        d["listings"] += 1; d["shops"].add(norm_text(r.get("shop_name") or "") or r["url"])
        if isinstance(r.get("price_vnd"), (int, float)) and r["price_vnd"] > 0:
            d["prices"].append(r["price_vnd"])
        n = parse_sold(r.get("sold_label"))
        if n is not None:
            d["sold_label_total"] += n; d["listings_with_sold"] += 1
    vel = dg.get("velocity") or {}
    for pp in vel.get("pairs", []):
        plat.setdefault(pp["platform"], {"listings": 0, "shops": set(), "prices": [], "sold_label_total": 0, "listings_with_sold": 0})
    platforms = {}
    for p, d in plat.items():
        pv = [x for x in vel.get("pairs", []) if x["platform"] == p]
        platforms[p] = {"listings": d["listings"], "shops": len(d["shops"]), "median_price": _median(d["prices"]),
                        "sold_label_total": d["sold_label_total"], "listings_with_sold": d["listings_with_sold"],
                        "units_per_30d": round(sum(x["per_30d"] for x in pv), 1) if pv else None}
    for need in ("shopee", "tiktok_shop"):
        if need not in platforms:
            missing.append(f"Tin đăng đúng model trên {need} (so sánh nền tảng)")
    if not vel:
        missing.append("Demand Velocity: đo lại nhãn 'đã bán' của cùng tin đăng sau ≥ 7 ngày")

    # Trả lời 9 câu hỏi (chỉ từ dữ liệu)
    pres = dg.get("presence") or {}
    ans = {
        "real_demand": ("Có bán ra (Velocity ≈ %s sp/30 ngày trên %d tin)" % (vel["units_per_30d"], vel["listings_measured"])) if vel else
                       ("Mới thấy hiện diện: %d tin, %d shop, nhãn 'đã bán' lũy kế %d; chưa biết bán trong bao lâu" % (pres.get("listings", 0), pres.get("shops", 0),
                        pres.get("cumulative_sold_label_total", 0)) if pres.get("listings") else "UNKNOWN: chưa có tin đăng của model"),
        "trend": (f"{(tg.get('stage') or 'unknown')}" + (" (bằng chứng gián tiếp, cấp nhóm sản phẩm)" if tg.get("basis") == "indirect" else "")) if tg["score"] is not None
                 else "UNKNOWN",
        "keywords": ", ".join(k["keyword"] for k in keywords if k.get("keyword")) or "UNKNOWN",
        "features": "; ".join(f"{t['theme']} ({t['mentions']})" for t in feats[:5]) or "UNKNOWN",
        "buying": "; ".join(f"{t['theme']} ({t['mentions']})" for t in buy[:5]) or "UNKNOWN",
        "non_buy": "; ".join(f"{t['theme']} ({t['mentions']})" for t in (nonbuy + pain)[:5]) or "UNKNOWN",
        "price": ((f"Tin có bán: {_vnd(price['price_of_listings_with_sales']['min'])}–{_vnd(price['price_of_listings_with_sales']['max'])} "
                   f"(n={len(sold_prices)})") if sold_prices else "UNKNOWN")
                 + "".join(f"; người mua nói: {t['theme']} ({t['mentions']}/{t['sample_size'] or '?'})" for t in price["buyer_comments"]),
        "seasonality": (seas["reason"] + (" Chuỗi tìm kiếm cấp nhóm sản phẩm (bằng chứng gián tiếp cho model)." if seas.get("scope") == "product" else ""))
                       if seas["status"] != "unknown" else "UNKNOWN: " + seas["reason"],
        "platforms": "; ".join(f"{p}: {d['listings']} tin, {d['shops']} shop" + (f", ≈ {d['units_per_30d']} sp/30 ngày" if d["units_per_30d"] is not None else "")
                               for p, d in sorted(platforms.items())) or "UNKNOWN",
    }
    answered = sum(1 for v in ans.values() if not str(v).startswith("UNKNOWN"))
    # Độ tin cậy: theo độ tin cậy nhóm Nhu cầu của Agent 07, giảm khi ít câu hỏi được trả lời
    conf = round((dg.get("confidence") or 0) * (0.5 + 0.5 * answered / len(QUESTIONS_03)), 3)
    if not vel:
        recs_out.append("Đo lại cùng URL tin đăng sau ≥ 7 ngày (Lazada/Tiki/Shopee) để có Velocity")
    if not themes:
        recs_out.append("Đọc ≥ 30 đánh giá của model trên 2 sàn, ghi demand (aspect, mentions, sample_size) cho từng chủ đề")
    if not kw:
        recs_out.append("Ghi gợi ý tìm kiếm Shopee/TikTok cho tên model (metric search_keyword)")
    if seas["status"] == "unknown":
        recs_out.append("Đọc Google Trends 5 năm theo tuần cho từ khóa model để xét mùa vụ")
    ev = list(dict.fromkeys(dg.get("evidence_ids", []) + [r["id"] for r in srch + eng + dem + kw]))
    return {"product_id": pid, "variant_key": variant, "product_name": e["product_name"], "model": e["model"], "agent": "demand_analyst",
            "config_version": dcfg["version"],
            "demand_score": {"score": dg["score"], "level": dg.get("level"), "reason": dg["reason"], "gate": e["gates"]["demand"]["status"]},
            "presence": pres, "velocity": vel or None, "search_interest": search, "engagement": engagement, "review_demand": review_demand,
            "trend_direction": {"stage": tg.get("stage"), "basis": tg.get("basis"), "reason": tg["reason"], "confidence": tg.get("confidence")},
            "keywords": keywords, "features": feats, "pain_points": pain, "buying_motivations": buy, "non_buy_reasons": nonbuy,
            "price_sensitivity": price, "seasonality": seas, "platform_differences": platforms,
            "questions": [{"key": k, "question": q, "answer": ans[k]} for k, q in QUESTIONS_03],
            "evidence": ev, "confidence": conf, "missing_data": list(dict.fromkeys(missing)), "recommendations": recs_out,
            "input_snapshot": e["input_snapshot"]}


def same_demand_analysis(old, new):
    strip = lambda d: json.dumps({k: v for k, v in d.items() if k not in ("id", "run_id", "collected_at", "kind", "write")}, sort_keys=True, ensure_ascii=False, default=str)
    return strip(old) == strip(new)


def latest_demand_analyses(store: Store):
    out = {}
    for r in store.records(kind="demand_analysis"):
        out[(r["product_id"], r.get("variant_key"))] = r
    return out


def render_demand_report(store: Store, items, run=None):
    L = [f"# Demand Analyst: nhu cầu theo model", "",
         f"Ngày {dt.date.today().strftime('%d/%m/%Y')}" + (f" · run `{run}`" if run else "") + " · Agent 03 Demand Analyst", "",
         "Tách 4 loại nhu cầu: tìm kiếm, tương tác, bán hàng (Presence/Velocity), đánh giá. UNKNOWN = chưa có dữ liệu, không phải 0.", "",
         "| Model | Demand Score | Mức | Velocity (sp/30 ngày) | Câu hỏi đã trả lời | Độ tin cậy |", "|---|---|---|---|---|---|"]
    for d in items:
        ans = sum(1 for q in d["questions"] if not str(q["answer"]).startswith("UNKNOWN"))
        L.append(f"| {d['model']['display'] if d['variant_key'] else d['product_name'] + ' (chưa xác định model)'} | "
                 f"{d['demand_score']['score'] if d['demand_score']['score'] is not None else 'chưa chấm'} | {d['demand_score']['level']} | "
                 f"{(d['velocity'] or {}).get('units_per_30d', 'chưa đo')} | {ans}/9 | {d['confidence']} |")
    for d in items:
        L += ["", f"## {d['model']['display'] if d['variant_key'] else d['product_name']}", ""]
        for q in d["questions"]:
            L.append(f"- **{q['question']}:** {q['answer']}")
        if d["pain_points"] or d["non_buy_reasons"]:
            L.append("- **Điểm đau / lý do không mua (chi tiết):** " + "; ".join(
                f"{t['theme']}: {t['mentions']}" + (f"/{t['sample_size']}" if t['sample_size'] else "") + f" ({t['reliability']}) {', '.join('`'+x+'`' for x in t['evidence'])}"
                for t in d["pain_points"] + d["non_buy_reasons"]))
        L.append(f"- **Bằng chứng:** " + (", ".join(f"`{x}`" for x in d["evidence"][:12]) + (" …" if len(d["evidence"]) > 12 else "") if d["evidence"] else "không có"))
        L.append(f"- **Độ tin cậy:** {d['confidence']}")
        L.append("- **Dữ liệu còn thiếu:** " + ("; ".join(d["missing_data"]) or "không"))
        sb = (d.get("substitutes") or {}).get("brands") or []
        if sb:
            L.append("- **Sản phẩm thay thế khác hãng (không gộp vào đối thủ cùng model):** " + "; ".join(
                f"{x['brand']} ({x['listings']} tin, {x['multi_sku_listings']} tin nhiều phân loại"
                + (f", giá thẻ {_vnd(x['card_price_range'][0])}–{_vnd(x['card_price_range'][1])}" if x["card_price_range"] else "")
                + (f", nhãn bán: {', '.join(x['sold_labels'][:4])}" if x["sold_labels"] else "") + ")" for x in sb)
                + ". " + d["substitutes"]["note"])
        L.append("- **Đề xuất:** " + ("; ".join(d["recommendations"]) or "không"))
    return "\n".join(L) + "\n"


def competitor_analysis(store: Store, pid, variant, now=None):
    """Agent 04 Competitor Analyst: đối thủ của một model từ dữ liệu đã có. Chỉ đọc kho, không thu thập, không suy diễn số liệu.
    Điểm cạnh tranh lấy từ Agent 07 (cùng quy tắc); phân bố giá theo sàn, mức tập trung, tính năng, phàn nàn, lợi thế, khoảng trống."""
    ccfg, w = cfg("competitor_analysis"), cfg("scoring_weights_v2")
    e = evaluate(store, pid, variant, now=now)
    by_id = {r["id"]: r for r in store.records()}
    used = [by_id[x["id"]] for x in e["input_snapshot"] if x["id"] in by_id]
    allrecs = list(by_id.values())
    links = variant_links(allrecs)
    L = list(latest_by_listing(used).values())  # mọi tin bán model (kể cả tin nhiều phân loại): đếm shop đối thủ
    L_exact = [r for r in L if listing_sku_scope(r, links, ccfg) == "exact"]  # chỉ tin đúng biến thể: tính giá
    cg = e["groups"]["competition"]
    missing = []
    # Trạng thái dữ liệu đối thủ: 0 shop chỉ có nghĩa khi ĐÃ tìm mà không thấy; chưa thu thập thì số shop là UNKNOWN
    searches = [r for r in allrecs if r["kind"] == "observation" and r.get("product_id") == pid and r.get("variant_key") == variant
                and r.get("metric") in ccfg.get("search_metrics", [])]
    if L:
        state = "verified" if len(L) >= ccfg["min_listings_verified"] else "insufficient"
    elif variant and searches and all((x.get("value") or 0) == 0 for x in searches):
        state = "searched_none_found"
    else:
        state = "not_collected"
    STATE_LABEL = {"verified": f"đã xác minh: {len(L)} tin đăng thật của model",
                   "insufficient": f"chưa đủ dữ liệu: mới {len(L)} tin (cần ≥ {ccfg['min_listings_verified']})",
                   "searched_none_found": f"đã tìm ({len(searches)} lần) nhưng không thấy tin nào của model; chưa phải bằng chứng ít cạnh tranh",
                   "not_collected": "chưa thu thập dữ liệu đối thủ cho model này (số shop = UNKNOWN, không phải 0)"}
    shops = {}
    for r in L:
        k = norm_text(r.get("shop_name") or "") or listing_key(r)
        d = shops.setdefault(k, {"shop": r.get("shop_name"), "listings": 0, "platforms": set(), "sold_label_total": 0, "prices": [], "ids": []})
        d["listings"] += 1; d["platforms"].add(platform_of(r)); d["ids"].append(r["id"])
        exact = listing_sku_scope(r, links, ccfg) == "exact"
        d.setdefault("multi_sku_listings", 0); d["multi_sku_listings"] += 0 if exact else 1
        if exact:  # nhãn bán và giá của tin nhiều phân loại không thuộc riêng biến thể này
            d["sold_label_total"] += parse_sold(r.get("sold_label")) or 0
            if isinstance(r.get("price_vnd"), (int, float)) and r["price_vnd"] > 0:
                d["prices"].append(r["price_vnd"])
    cc = w["competition"]
    brand = variant.split("-")[0] if variant else ""
    pats = [norm_text(x) for x in cc["incumbent_patterns"] + cc["brand_aliases"].get(brand, [])]
    comp_list = []
    for d in sorted(shops.values(), key=lambda d: (-d["sold_label_total"], d["shop"] or "")):
        comp_list.append({"shop": d["shop"], "listings": d["listings"], "platforms": sorted(d["platforms"]), "sold_label_total": d["sold_label_total"],
                          "min_price": min(d["prices"]) if d["prices"] else None, "multi_sku_listings": d.get("multi_sku_listings", 0),
                          "type": "hãng / nhà phân phối / official" if any(x and x in norm_text(d["shop"] or "") for x in pats) else "shop thường",
                          "evidence": d["ids"]})
    # Phân bố giá theo sàn
    dist = {}
    for plat in sorted({platform_of(r) for r in L}):
        ps = sorted(float(r["price_vnd"]) for r in L_exact if platform_of(r) == plat and isinstance(r.get("price_vnd"), (int, float)) and r["price_vnd"] > 0)
        if len(ps) >= ccfg["min_listings_for_distribution"]:
            dist[plat] = {"n": len(ps), "min": round(ps[0]), "p25": round(_quantile(ps, .25)), "median": round(_quantile(ps, .5)),
                          "p75": round(_quantile(ps, .75)), "max": round(ps[-1])}
        else:
            dist[plat] = {"n": len(ps), "note": f"dưới {ccfg['min_listings_for_distribution']} tin đúng biến thể: không tính phân bố", "prices": [round(x) for x in ps]}
        nm = sum(1 for r in L if platform_of(r) == plat and r not in L_exact)
        if nm:
            dist[plat]["excluded_multi_sku"] = nm
    for plat in ccfg["required_platforms"]:
        if plat not in dist:
            missing.append(f"Tin đăng đúng model trên {plat}")
    # Mức tập trung
    sold_total = sum(c["sold_label_total"] for c in comp_list)
    with_sold = [c for c in comp_list if c["sold_label_total"] > 0]
    if len(with_sold) >= 2:
        shares = [c["sold_label_total"] / sold_total for c in with_sold]
        conc = {"basis": "nhãn 'đã bán' lũy kế", "top_shop": with_sold[0]["shop"], "top_share": round(shares[0], 2),
                "hhi": round(sum(x * x for x in shares), 3), "shops_with_sales": len(with_sold),
                "note": "Nhãn lũy kế gồm cả doanh số cũ; chỉ cho biết ai từng bán nhiều, không phải thị phần hiện tại."}
    else:
        conc = {"basis": None, "note": "Dưới 2 shop có nhãn 'đã bán': chưa nói được mức tập trung."}
        missing.append("≥ 2 shop có nhãn 'đã bán' để đo mức tập trung")
    inc = [c for c in comp_list if c["type"] != "shop thường"]
    conc["incumbent_listings"] = sum(c["listings"] for c in inc)
    conc["incumbent_shops"] = [c["shop"] for c in inc]
    # Tính năng / phàn nàn / lợi thế (từ tin đăng và đánh giá người mua do Agent 03 ghi)
    dcfg = cfg("demand_analysis")
    # Chỉ phản hồi về sản phẩm cụ thể (có variant_key) mới là phàn nàn/khen về hàng đối thủ; vấn đề chung của người mua thuộc Agent 03
    dem = [r for r in used if r["kind"] == "demand" and r.get("variant_key")]
    def asp(r):
        return r.get("aspect") or dcfg["aspect_from_type"].get(r["type"], "pain_point")
    def themes(aspects):
        out = {}
        for r in dem:
            if asp(r) not in aspects:
                continue
            t = out.setdefault(norm_text(r["theme"]), {"theme": r["theme"], "mentions": 0, "sample_size": 0, "listings": set(), "evidence": []})
            t["mentions"] += r.get("mentions") or 1; t["sample_size"] += r.get("sample_size") or 0
            t["listings"].add(r.get("source_url") or r["id"]); t["evidence"].append(r["id"])
        res = []
        for t in out.values():
            small = (t["sample_size"] and t["sample_size"] < ccfg["small_sample_reviews"]) or t["mentions"] < ccfg["gap_min_mentions"]
            res.append({"theme": t["theme"], "mentions": t["mentions"], "sample_size": t["sample_size"] or None, "listings": len(t["listings"]),
                        "evidence": t["evidence"], "reliability": "mẫu nhỏ, chỉ là tín hiệu" if small else "lặp lại trong mẫu đủ lớn",
                        "_strong": t["mentions"] >= ccfg["gap_min_mentions"] and len(t["listings"]) >= ccfg["gap_min_listings"]})
        return sorted(res, key=lambda x: -x["mentions"])
    complaints = themes({"pain_point", "return_reason", "non_buy_reason"})
    praised = themes({"feature", "buying_reason", "use_case"})
    lw = [{"listing": r["id"], "shop": r.get("shop_name"), "weaknesses": r.get("weaknesses")} for r in L if r.get("weaknesses")]
    ls = [{"listing": r["id"], "shop": r.get("shop_name"), "strengths": r.get("strengths")} for r in L if r.get("strengths")]
    features = {"buyer_valued": [{k: v for k, v in t.items() if k != "_strong"} for t in praised], "listing_strengths": ls,
                "note": "Tính năng người mua nhắc tới trong đánh giá; chưa có bảng so sánh thông số giữa các model."}
    if not dem:
        missing.append("Đánh giá người mua (Agent 03) để biết phàn nàn và tính năng được khen")
    # Lợi thế có thể có và khoảng trống: chỉ nêu khi có bằng chứng, mạnh/yếu theo số lượt nhắc và số tin
    gaps = []
    for t in complaints:
        gaps.append({"gap": f"Giải quyết: {t['theme']}", "strength": "khoảng trống có cơ sở" if t["_strong"] else "giả thuyết (mẫu nhỏ)",
                     "evidence": t["evidence"]})
    if dist:
        meds = {p: d["median"] for p, d in dist.items() if "median" in d}
        if len(meds) >= 2 and max(meds.values()) > 1.15 * min(meds.values()):
            gaps.append({"gap": "Chênh giá trung vị giữa các sàn: " + ", ".join(f"{p} {_vnd(v)}" for p, v in meds.items()),
                         "strength": "quan sát", "evidence": [r["id"] for r in L]})
    advantages = []
    # Lợi thế chỉ nêu khi đã xác minh đủ tin đăng thật; không suy lợi thế từ việc chưa thu thập hay chưa tìm thấy
    if state == "verified" and not inc:
        advantages.append({"advantage": f"Chưa thấy shop hãng/nhà phân phối trong {len(L)} tin đã thu: cửa cho shop đại lý (chỉ trong mẫu)",
                           "evidence": [r["id"] for r in L]})
    if state == "verified" and conc.get("top_share") is not None and conc["top_share"] < 0.4:
        advantages.append({"advantage": f"Không shop nào chiếm ≥ 40% nhãn 'đã bán' (shop đầu {conc['top_share']:.0%})", "evidence": [r["id"] for r in L]})
    ev = list(dict.fromkeys([r["id"] for r in L] + [r["id"] for r in dem]))
    conf = round((cg.get("confidence") or 0) * (1 if dem else 0.7), 3)
    rec = []
    if "shopee" not in dist:
        rec.append("Thu tin đăng đúng model trên Shopee (anh Khoa giải CAPTCHA)")
    if len(L) < 5:
        rec.append("Thu thêm tin đăng đúng model (≥ 5) để phân bố giá đáng tin")
    if not dem or all(t["reliability"].startswith("mẫu nhỏ") for t in complaints):
        rec.append("Đọc thêm đánh giá (≥ 30) của các đối thủ chính để xác nhận phàn nàn")
    subs = substitutes(store, pid, variant, allrecs, links, ccfg)
    if state in ("not_collected", "searched_none_found"):
        rec.insert(0, "Thu tin đăng đúng model trên Lazada/Shopee/TikTok Shop; ghi số kết quả tìm kiếm (kể cả 0) bằng observation search_result_count")
    return {"product_id": pid, "variant_key": variant, "product_name": e["product_name"], "model": e["model"], "agent": "competitor_analyst",
            "config_version": ccfg["version"], "competitor_state": {"state": state, "label": STATE_LABEL[state], "searches": [x["id"] for x in searches]},
            "substitutes": subs,
            "competitor_score": {"score": cg["score"], "band": cg.get("band"), "reason": cg["reason"], "components": cg.get("components")},
            "competitor_count": {"shops": None if state == "not_collected" else len(comp_list), "listings": None if state == "not_collected" else len(L),
                                 "listings_exact_variant": len(L_exact), "platforms": sorted({platform_of(r) for r in L})},
            "competitors": comp_list, "price_distribution": dist, "brand_concentration": conc, "feature_comparison": features,
            "complaints": [{k: v for k, v in t.items() if k != "_strong"} for t in complaints], "listing_weaknesses": lw,
            "advantages": advantages, "market_gaps": gaps, "evidence": ev, "confidence": conf,
            "missing_data": list(dict.fromkeys(missing)), "recommendations": rec, "input_snapshot": e["input_snapshot"]}


def substitutes(store, pid, variant, allrecs, links, ccfg):
    """Sản phẩm thay thế khác hãng cùng chức năng: (1) listing ghi substitute_for = model này, (2) tin của model khác cùng sản phẩm,
    cùng giao thức, khác hãng (theo hồ sơ model). Không gộp vào đối thủ cùng model, không cộng nhãn bán, không dùng cho trung vị."""
    if not variant:
        return {"brands": [], "note": "Chưa xác định model."}
    profs = variant_profiles(allrecs)
    me = profs.get((pid, variant))
    rows = [r for r in allrecs if r["kind"] == "listing" and r.get("product_id") == pid and r.get("substitute_for") == variant]
    others = []
    if me:
        for (p_, v_), pr in profs.items():
            if p_ == pid and v_ != variant and not pr.get("is_group") and pr["protocol"] == me["protocol"] and norm_text(pr["brand"]) != norm_text(me["brand"]):
                others.append(v_)
        rows += [r for r in allrecs if r["kind"] == "listing" and r.get("product_id") == pid and record_variant(r, links) in others]
    rows = list(latest_by_listing(rows).values())
    by = {}
    for r in rows:
        v_ = record_variant(r, links)
        b_ = (profs[(pid, v_)]["brand"] if v_ and (pid, v_) in profs else r.get("brand")) or "không rõ hãng"
        d = by.setdefault(b_, {"brand": b_, "model_or_group": v_ or "tin thay thế", "listings": 0, "multi_sku_listings": 0, "platforms": set(),
                                "exact_prices": [], "card_prices": [], "sold_labels": [], "evidence": []})
        d["listings"] += 1; d["platforms"].add(platform_of(r)); d["evidence"].append(r["id"])
        if isinstance(r.get("price_vnd"), (int, float)) and r["price_vnd"] > 0:
            (d["exact_prices"] if listing_sku_scope(r, links, ccfg) == "exact" else d["card_prices"]).append(r["price_vnd"])
        if listing_sku_scope(r, links, ccfg) != "exact":
            d["multi_sku_listings"] += 1
        if r.get("sold_label"):
            d["sold_labels"].append(r["sold_label"])
    out = []
    for d in sorted(by.values(), key=lambda d: -d["listings"]):
        out.append({"brand": d["brand"], "model_or_group": d["model_or_group"], "listings": d["listings"], "multi_sku_listings": d["multi_sku_listings"],
                    "platforms": sorted(d["platforms"]),
                    "card_price_range": [round(min(d["card_prices"] + d["exact_prices"])), round(max(d["card_prices"] + d["exact_prices"]))]
                    if d["card_prices"] or d["exact_prices"] else None,
                    "sold_labels": d["sold_labels"], "evidence": d["evidence"]})
    return {"brands": out, "compared_variants": others,
            "note": "Chỉ nêu khoảng giá thẻ: tin nhiều phân loại hiện giá phân loại rẻ nhất và model khác có thể khác số nút, nên không tính trung vị so với model này. "
                    "Nhãn 'đã bán' gộp mọi phân loại và có thể làm tròn: chỉ để thấy mặt bằng, không phải thị phần."}


def latest_competitor_analyses(store: Store):
    out = {}
    for r in store.records(kind="competitor_analysis"):
        out[(r["product_id"], r.get("variant_key"))] = r
    return out


def render_competitor_report(store: Store, items, run=None):
    L = ["# Competitor Analyst: đối thủ theo model", "",
         f"Ngày {dt.date.today().strftime('%d/%m/%Y')}" + (f" · run `{run}`" if run else "") + " · Agent 04 Competitor Analyst", "",
         "Chỉ dùng dữ liệu đã có trong kho. Phàn nàn từ vài đánh giá là tín hiệu, không phải kết luận.", "",
         "| Model | Trạng thái dữ liệu đối thủ | Điểm cạnh tranh | Shop | Tin (đúng biến thể) | Sàn | Giá trung vị (tin đúng biến thể) | Độ tin cậy |",
         "|---|---|---|---|---|---|---|---|"]
    def name(d):
        return d["model"]["display"] if d["variant_key"] else d["product_name"] + " (chưa xác định model)"
    for d in items:
        med = "; ".join(f"{p} {_vnd(x['median'])}" for p, x in d["price_distribution"].items() if "median" in x) or "chưa đủ tin"
        sc = d["competitor_score"]["score"]
        cc_ = d["competitor_count"]; st_ = (d.get("competitor_state") or {}).get("state", "-")
        unk = lambda x: "UNKNOWN" if x is None else x
        L.append(f"| {name(d)} | {st_} | {sc if sc is not None else 'chưa chấm'} | {unk(cc_['shops'])} | {unk(cc_['listings'])} ({cc_.get('listings_exact_variant', '-')}) | "
                 f"{', '.join(cc_['platforms']) or '-'} | {med} | {d['confidence']} |")
    for d in items:
        if not d["competitor_count"]["listings"]:
            continue
        L += ["", f"## {name(d)}", "", f"- **Trạng thái dữ liệu đối thủ:** {(d.get('competitor_state') or {}).get('label', '-')}",
              f"- **Điểm cạnh tranh:** {d['competitor_score']['score']} · {d['competitor_score']['reason']}"]
        L.append("- **Đối thủ:** " + "; ".join(f"{c['shop']} ({c['type']}, {c['listings']} tin, đã bán lũy kế {c['sold_label_total']}"
                                                 + (f", từ {_vnd(c['min_price'])}" if c['min_price'] else "") + ")" for c in d["competitors"][:8]))
        L.append("- **Phân bố giá:** " + "; ".join(
            (f"{p}: {_vnd(x['min'])} / P25 {_vnd(x['p25'])} / trung vị {_vnd(x['median'])} / P75 {_vnd(x['p75'])} / {_vnd(x['max'])} (n={x['n']})" if "median" in x
             else f"{p}: {x['note']}") for p, x in d["price_distribution"].items()))
        bc = d["brand_concentration"]
        L.append("- **Mức tập trung:** " + (f"shop đầu {bc['top_shop']} chiếm {bc['top_share']:.0%} nhãn 'đã bán' lũy kế, HHI {bc['hhi']}. " if bc.get("basis") else "")
                 + f"{bc['incumbent_listings']} tin của hãng/nhà phân phối. {bc['note']}")
        L.append("- **Tính năng người mua khen:** " + ("; ".join(f"{t['theme']} ({t['mentions']}/{t['sample_size'] or '?'}, {t['reliability']})"
                                                         for t in d["feature_comparison"]["buyer_valued"]) or "chưa có dữ liệu"))
        L.append("- **Phàn nàn:** " + ("; ".join(f"{t['theme']} ({t['mentions']}/{t['sample_size'] or '?'}, {t['reliability']})" for t in d["complaints"]) or "chưa có dữ liệu"))
        L.append("- **Lợi thế có thể có:** " + ("; ".join(a["advantage"] for a in d["advantages"]) or "chưa có bằng chứng"))
        L.append("- **Khoảng trống thị trường:** " + ("; ".join(f"{g['gap']} ({g['strength']})" for g in d["market_gaps"]) or "chưa có bằng chứng"))
        L.append(f"- **Độ tin cậy:** {d['confidence']} · **Dữ liệu còn thiếu:** " + ("; ".join(d["missing_data"]) or "không"))
        L.append("- **Đề xuất:** " + ("; ".join(d["recommendations"]) or "không"))
    return "\n".join(L) + "\n"


def remeasure_plan(store: Store, today=None, product=None, variant=None):
    """Kế hoạch đo lại nhãn 'đã bán' của CÙNG tin đăng (sàn + mã tin). Chỉ đọc kho, không ghi gì: chưa đến hạn thì không có bản ghi mới.
    Mỗi tin: các lần đã đo (ngày, nhãn, độ chính xác), hạn đo lại, cách truy cập, cảnh báo nhãn làm tròn / tin nhiều phân loại."""
    rc = cfg("remeasure")
    today = today or dt.date.today()
    start = dt.date.fromisoformat(rc["start_date"])
    allrecs = store.records()
    links = variant_links(allrecs)
    ccfg = cfg("competitor_analysis")
    series = {}
    for r in allrecs:
        if r["kind"] != "listing":
            continue
        v = record_variant(r, links)
        if not v or (product and r.get("product_id") != product) or (variant and v != variant):
            continue
        k = listing_key(r)
        d = series.setdefault(k, {"listing": k, "product_id": r["product_id"], "variant_key": v, "platform": platform_of(r), "url": r["url"],
                                  "title": r.get("title"), "shop": r.get("shop_name"), "points": []})
        d["url"], d["sku_scope"] = r["url"], listing_sku_scope(r, links, ccfg)
        info = sold_label_info(r.get("sold_label"))
        d["points"].append({"date": (r.get("observed_at") or r["collected_at"])[:10], "label": r.get("sold_label"), "value": info["value"],
                            "precision": info["precision"], "id": r["id"]})
    for r in allrecs:  # nhãn 'Đã bán' trên web hãng (nguồn tự báo): đo lại cùng URL để tham khảo, không chấm điểm
        if r["kind"] == "observation" and r.get("metric") == "sold_count_label" and r.get("variant_key") and r.get("source_url"):
            if (product and r.get("product_id") != product) or (variant and r["variant_key"] != variant):
                continue
            k = f"{r.get('platform') or domain(r['source_url'])}:{r['source_url'].rstrip('/').lower()}"
            d = series.setdefault(k, {"listing": k, "product_id": r["product_id"], "variant_key": r["variant_key"], "platform": r.get("platform"),
                                      "url": r["source_url"], "title": None, "shop": None, "points": [], "sku_scope": "exact", "self_reported": True})
            info = sold_label_info(r.get("value"))
            d["points"].append({"date": r.get("observed_at") or r["collected_at"][:10], "label": r.get("value"), "value": info["value"],
                                "precision": info["precision"], "id": r["id"]})
    rows = []
    for d in series.values():
        pts = sorted(d["points"], key=lambda p: p["date"])
        last = pts[-1]
        due = max(start, dt.date.fromisoformat(last["date"]) + dt.timedelta(days=rc["min_days"]))
        warn = []
        if last["value"] is None:
            warn.append("lần đo gần nhất không có nhãn 'đã bán': đo lại chỉ có ích nếu tin đã có nhãn")
        elif last["precision"] != "exact":
            warn.append(f"nhãn làm tròn '{last['label']}': đo lại chỉ biết có vượt mốc hay không, không tính được tốc độ")
        if d.get("sku_scope") == "multi_sku":
            warn.append("tin nhiều phân loại: tốc độ là của cả dòng, không riêng biến thể")
        if d.get("self_reported"):
            warn.append("số do web hãng tự hiển thị: chỉ tham khảo, không chấm điểm")
        days_measured = sorted({p["date"] for p in pts})
        velocity = None
        if len(days_measured) >= 2:
            p0, p1 = pts[0], pts[-1]
            span = (dt.date.fromisoformat(p1["date"]) - dt.date.fromisoformat(p0["date"])).days
            if p0["precision"] == p1["precision"] == "exact" and span >= rc["min_days"] and p1["value"] >= p0["value"]:
                velocity = {"from": p0["date"], "to": p1["date"], "days": span, "delta": p1["value"] - p0["value"],
                            "per_30d": round((p1["value"] - p0["value"]) * 30 / span, 1)}
            elif span >= rc["min_days"]:
                velocity = {"from": p0["date"], "to": p1["date"], "days": span, "delta": None,
                            "why_not": "nhãn làm tròn ở một lần đo" if "rounded" in (p0["precision"], p1["precision"]) else
                            ("nhãn giảm (đổi tin hoặc đặt lại)" if (p1["value"] or 0) < (p0["value"] or 0) else "thiếu nhãn")}
        rows.append({**{k: d.get(k) for k in ("listing", "product_id", "variant_key", "platform", "url", "title", "shop", "sku_scope")},
                     "measurements": pts, "last_measured": last["date"], "due": due.isoformat(),
                     "status": "đến hạn" if today >= due else "chưa đến hạn", "access": rc["access"].get(d["platform"] or "", "chưa có hướng dẫn"),
                     "warnings": warn, "velocity": velocity, "scored": not d.get("self_reported") and d.get("sku_scope") == "exact"})
    rows.sort(key=lambda r: (r["due"], r["variant_key"] or "", r["platform"] or "", r["listing"]))
    return {"today": today.isoformat(), "start_date": rc["start_date"], "min_days": rc["min_days"], "config_version": rc["version"],
            "due": sum(1 for r in rows if r["status"] == "đến hạn"), "total": len(rows), "rows": rows}


def render_remeasure_plan(plan):
    L = ["# Kế hoạch đo lại nhãn 'đã bán' (Demand Velocity)", "",
         f"Ngày {plan['today']} · bắt đầu đo lại từ {plan['start_date']} · cách lần đo trước ≥ {plan['min_days']} ngày · "
         f"{plan['due']}/{plan['total']} tin đến hạn", "",
         "Quy tắc: chỉ đo lại đúng tin cũ (cùng sàn + cùng mã tin), ghi bản ghi listing mới cùng URL với observed_at = ngày đo; "
         "chưa đến hạn thì không ghi gì. Nhãn làm tròn ('2k+') không dùng để tính tốc độ.", "",
         "| Model | Sàn | Tin | Lần đo | Nhãn gần nhất | Hạn | Trạng thái | Chấm điểm | Cảnh báo |", "|---|---|---|---|---|---|---|---|---|"]
    for r in plan["rows"]:
        last = r["measurements"][-1]
        L.append(f"| {r['variant_key']} | {r['platform']} | [{r['listing']}]({r['url']}) | {len(r['measurements'])} | "
                 f"{last['label'] or '-'} ({last['precision']}) | {r['due']} | {r['status']} | {'có' if r['scored'] else 'không'} | {'; '.join(r['warnings']) or '-'} |")
    return "\n".join(L) + "\n"


PIPELINE_STEPS = ("readiness", "demand", "competitors", "evaluate")


def _write_dedup(store, run, kind, rec, prev, same):
    if prev and same(prev, rec):
        return prev, "unchanged"
    return store.append(run, kind, rec), "new"


def run_pipeline(store: Store, run, targets):
    """Phase 4: chạy các Agent chỉ đọc kho (03 Demand, 04 Competitor, 07 Evaluator) theo đúng thứ tự cho từng model, chống ghi trùng.
    Agent 01/02/05/06 cần thu thập (trình duyệt/người dùng) nên không chạy ở đây; việc của chúng xuất hiện trong next_actions."""
    pd, pc, pe = latest_demand_analyses(store), latest_competitor_analyses(store), latest_evaluations(store)
    rows = []
    for pid, v in targets:
        tm, t0 = {}, time.perf_counter()
        rd = readiness(store, pid, v); tm["readiness"] = time.perf_counter() - t0; t0 = time.perf_counter()
        d, wd = _write_dedup(store, run, "demand_analysis", demand_analysis(store, pid, v), pd.get((pid, v)), same_demand_analysis)
        tm["agent03_demand"] = time.perf_counter() - t0; t0 = time.perf_counter()
        c, wc = _write_dedup(store, run, "competitor_analysis", competitor_analysis(store, pid, v), pc.get((pid, v)), same_demand_analysis)
        tm["agent04_competitors"] = time.perf_counter() - t0; t0 = time.perf_counter()
        e, we = _write_dedup(store, run, "evaluation", evaluate(store, pid, v), pe.get((pid, v)), same_evaluation)
        tm["agent07_evaluate"] = time.perf_counter() - t0
        rows.append({"product_id": pid, "variant_key": v, "readiness": rd, "demand": d, "competitors": c, "evaluation": e,
                     "writes": {"demand_analysis": wd, "competitor_analysis": wc, "evaluation": we},
                     "timings_s": {k: round(x, 3) for k, x in tm.items()}})
    return rows


def render_pipeline_report(rows, run=None):
    def name(e):
        return (e.get("model") or {}).get("display") if e.get("variant_key") else f"{e.get('product_name')} (chưa xác định model)"
    L = ["# AI Product Hunter: báo cáo tổng hợp theo model", "",
         f"Ngày {dt.date.today().strftime('%d/%m/%Y')}" + (f" · run `{run}`" if run else "") + " · Agent 03 → 04 → 07 trên dữ liệu đã có trong kho", "",
         "Thứ tự: Demand Analyst (03) và Competitor Analyst (04) tóm tắt dữ liệu; Opportunity Evaluator (07) chấm điểm và kết luận từ dữ liệu gốc. "
         "Bảng xếp theo Opportunity Score giảm dần.", "",
         "| Model | Trạng thái | Điểm | Phủ | Nhu cầu | Velocity sp/30 ngày | Đối thủ (shop) | Phàn nàn chính | Câu hỏi nhu cầu đã trả lời |",
         "|---|---|---|---|---|---|---|---|---|"]
    rows = sorted(rows, key=lambda r: (r["evaluation"]["total"] is None, -(r["evaluation"]["total"] or 0)))
    for r in rows:
        e, d, c = r["evaluation"], r["demand"], r["competitors"]
        ans = sum(1 for q in d["questions"] if not str(q["answer"]).startswith("UNKNOWN"))
        comp = c["complaints"][0] if c["complaints"] else None
        L.append(f"| {name(e)} | {e['status'].replace('_', ' ')} | {e['total'] if e['total'] is not None else 'chưa có'} | {e['weight_coverage']}% | "
                 f"{d['demand_score']['level']} | {(d['velocity'] or {}).get('units_per_30d', 'chưa đo')} | {c['competitor_count']['shops'] if c['competitor_count']['shops'] is not None else 'UNKNOWN (chưa thu thập)'} | "
                 f"{(comp['theme'] + ' (' + str(comp['mentions']) + '/' + str(comp['sample_size'] or '?') + ')') if comp else '-'} | {ans}/9 |")
    owners = {}
    for r in rows:
        for a in r["evaluation"]["next_actions"]:
            owners.setdefault(a.get("owner") or "?", {}).setdefault(a["action"], []).append(name(r["evaluation"]))
    L += ["", "## Việc tiếp theo theo người/Agent phụ trách", ""]
    for o in sorted(owners):
        L.append(f"### {o}")
        for act, models in sorted(owners[o].items(), key=lambda x: -len(x[1])):
            L.append(f"- {act} — {len(models)} model" + (f": {', '.join(models[:4])}{' …' if len(models) > 4 else ''}" if len(models) <= 6 else ""))
        L.append("")
    tot = {}
    for r in rows:
        for k, x in (r.get("timings_s") or {}).items():
            tot[k] = tot.get(k, 0) + x
    if tot:
        L += ["## Thời gian chạy (giây, cộng mọi model)", "", "| Bước | Giây |", "|---|---|"] + [f"| {k} | {x:.2f} |" for k, x in tot.items()] + \
             [f"| Tổng {len(rows)} model | {sum(tot.values()):.2f} |", ""]
    L += ["## Ghi chú", "- GO cần mọi Gate đạt, điểm ≥ 75 và phủ trọng số ≥ 80%. Thiếu dữ liệu cho ra NEED MORE DATA, không phải kết luận xấu.",
          "- Chi tiết từng Agent: chạy `aph.py demand|competitors|eval-report --out …` cho báo cáo riêng."]
    return "\n".join(L) + "\n"


def readiness(store: Store, pid, variant):
    """Kiểm tra đầu vào của một model trước khi đánh giá: đủ / thiếu / không hợp lệ, và Agent nào bổ sung. Không ghi gì."""
    e = evaluate(store, pid, variant)
    g, gt = e["groups"], e["gates"]
    owner = {"demand": "Agent 03", "trend": "Agent 02", "competition": "Agent 04 / Agent 06", "supplier": "Agent 05", "financial": "Agent 06 + anh Khoa"}
    checks = [{"item": "Hồ sơ model (hãng, model, giao thức)", "ok": bool(e["model"].get("profile_id")) and not e["model"].get("is_group"),
               "detail": e["model"]["display"], "owner": "Agent 06 / Agent 07"}]
    for k in GROUPS:
        checks.append({"item": cfg("scoring_weights_v2")["groups"][k]["label"] + ": có dữ liệu để chấm", "ok": g[k]["score"] is not None,
                       "detail": g[k]["reason"][:160], "owner": owner[k]})
    checks.append({"item": "Demand Velocity", "ok": bool((g["demand"].get("velocity") or {}).get("listings_measured")),
                   "detail": "đo lại cùng tin đăng sau ≥ 7 ngày", "owner": "Agent 03"})
    checks.append({"item": "Giá vốn đã xác minh + đủ chi phí", "ok": gt["financial"]["status"] == "pass", "detail": gt["financial"]["reason"],
                   "owner": "anh Khoa → Agent 06"})
    invalid = {"unassigned": e["identity"]["unassigned_ids"], "own_shop": e["identity"]["own_shop_ids"]}
    return {"product_id": pid, "variant_key": variant, "model": e["model"]["display"], "ready_for_go": all(c["ok"] for c in checks),
            "checks": checks, "excluded": invalid, "expected_status": e["status"]}


def product_variants(store: Store, pid):
    """Các variant_key có dữ liệu theo model của sản phẩm (tin đăng, trần giá nhập, variant_link). Không có -> [None]."""
    allrecs = store.records()
    links = variant_links(allrecs)
    vs = {record_variant(r, links) for r in allrecs if r.get("product_id") == pid and is_model_specific(r)}
    vs |= {r["variant_key"] for r in allrecs if r["kind"] in ("variant_link", "variant_profile") and r["product_id"] == pid}
    vs.discard(None)
    return sorted(vs) or [None]


def latest_evaluations(store: Store, version="v2"):
    out = {}
    for r in store.records(kind="evaluation"):
        if str(r.get("weights_version", "")).startswith(version):
            out[(r["product_id"], r.get("variant_key"))] = r
    return out


STATUS_ORDER = {"GO": 0, "CONDITIONAL_GO": 1, "NEED_MORE_DATA": 2, "NO_GO": 3}


RANKING_KINDS = {
    "score": "Xếp hạng theo Opportunity Score: giảm dần theo điểm chuẩn hóa; model chưa có điểm đứng cuối. Điểm chỉ tính trên phần trọng số có dữ liệu, "
             "nên phải đọc kèm cột Phủ trọng số.",
    "verification": "Thứ tự ưu tiên xác minh (không phải xếp hạng cơ hội): theo trạng thái (GO → CONDITIONAL GO → NEED MORE DATA → NO GO), "
                    "rồi model có nhiều dữ liệu hơn (phủ trọng số cao hơn), rồi ít Gate chưa đủ hơn, rồi điểm.",
}


def ranking(store: Store, kind="score"):
    if kind not in RANKING_KINDS:
        raise APHError(f"--by phải là {list(RANKING_KINDS)}")
    rows = []
    for (pid, v), e in latest_evaluations(store).items():
        rows.append({"product_id": pid, "variant_key": v, "name": e.get("product_name"),
                     "model": ((e.get("model") or {}).get("display") or v) if v else f"{e.get('product_name')} (chưa xác định model)",
                     "status": e["status"], "total": e["total"],
                     "weight_coverage": e["weight_coverage"], "data_confidence": e["data_confidence"], "evaluation_id": e["id"],
                     "gates_open": sum(1 for g in e["gates"].values() if g["status"] != "pass"),
                     "evaluated_at": e["collected_at"], "next_action": (e["next_actions"] or [{}])[0].get("action")})
    if kind == "score":
        return sorted(rows, key=lambda r: (r["total"] is None, -(r["total"] or 0), -r["weight_coverage"]))
    return sorted(rows, key=lambda r: (STATUS_ORDER[r["status"]], -r["weight_coverage"], r["gates_open"], -(r["total"] or -1)))


def eval_diff(store: Store, pid, variant, a=None, b=None):
    """So sánh 2 bản đánh giá v2 của cùng model: điểm, độ phủ, độ tin cậy, trạng thái, Gate, và bản ghi đầu vào thêm/bớt (nguyên nhân)."""
    evs = [r for r in store.records(pid, "evaluation") if str(r.get("weights_version", "")).startswith("v2") and r.get("variant_key") == variant]
    pick = lambda i: next((r for r in evs if r["id"] == i), None)
    if a or b:
        ea, eb = pick(a), pick(b)
        if not ea or not eb:
            raise APHError("Không tìm thấy 2 bản đánh giá v2 với id đã cho cho đúng sản phẩm và model")
    else:
        if len(evs) < 2:
            raise APHError(f"Cần ≥ 2 bản đánh giá v2 cho model này, mới có {len(evs)}")
        ea, eb = evs[-2], evs[-1]
    ia, ib = {x["id"] for x in ea["input_snapshot"]}, {x["id"] for x in eb["input_snapshot"]}
    by_id = {r["id"]: r for r in store.records(include_retracted=True)}
    retracted = {r["target"] for r in store.records(kind="retraction")}
    groups = {}
    for g in GROUPS:
        ga, gb = ea["groups"][g], eb["groups"][g]
        if (ga["score"], ga["confidence"]) != (gb["score"], gb["confidence"]):
            groups[g] = {"from": ga["score"], "to": gb["score"], "confidence_from": ga["confidence"], "confidence_to": gb["confidence"],
                         "reason_to": gb["reason"]}
    gates = {k: {"from": ea["gates"][k]["status"], "to": eb["gates"][k]["status"], "reason_to": eb["gates"][k]["reason"]}
             for k in eb["gates"] if ea["gates"].get(k, {}).get("status") != eb["gates"][k]["status"]}
    return {"product_id": pid, "variant_key": variant, "from": {"id": ea["id"], "at": ea["collected_at"]}, "to": {"id": eb["id"], "at": eb["collected_at"]},
            "weights_version": {"from": ea.get("weights_version"), "to": eb.get("weights_version")},
            "status": {"from": ea["status"], "to": eb["status"]}, "total": {"from": ea["total"], "to": eb["total"]},
            "weight_coverage": {"from": ea["weight_coverage"], "to": eb["weight_coverage"]},
            "data_confidence": {"from": ea["data_confidence"], "to": eb["data_confidence"]},
            "groups_changed": groups, "gates_changed": gates,
            "inputs_added": [{"id": i, "kind": by_id[i]["kind"]} for i in sorted(ib - ia) if i in by_id],
            "inputs_removed": [{"id": i, "kind": by_id[i]["kind"], "retracted": i in retracted} for i in sorted(ia - ib) if i in by_id]}


def render_eval_report(store: Store, title, run_id=None):
    """Báo cáo tiếng Việt từ bản đánh giá v2 mới nhất của mỗi model: 2 bảng (xếp hạng theo điểm, ưu tiên xác minh) + 16 mục mỗi model."""
    w = cfg("scoring_weights_v2")
    evs = latest_evaluations(store)
    conf_lbl = lambda c: "Cao" if c >= 70 else "Trung bình" if c >= 40 else "Thấp"
    out = [f"# {title}", "", f"Ngày {dt.date.today():%d/%m/%Y}" + (f" · run `{run_id}`" if run_id else "")
           + f" · Agent 07 Opportunity Evaluator · trọng số {w['version']}", ""]
    sc = lambda r: f"{r['total']:.0f}" if r["total"] is not None else "Chưa có điểm"
    out += ["## Xếp hạng theo Opportunity Score", "", f"Tiêu chí: {RANKING_KINDS['score']}", "",
            "| # | Model (thương hiệu · tên · giao thức) | Điểm | Phủ trọng số | Data Confidence | Trạng thái |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(ranking(store, "score"), 1):
        out.append(f"| {i} | {r['model']} | {sc(r)} | {r['weight_coverage']}% | {r['data_confidence']} ({conf_lbl(r['data_confidence'])}) | {STATUS_LABEL[r['status']]} |")
    out += ["", "## Thứ tự ưu tiên xác minh", "", f"Tiêu chí: {RANKING_KINDS['verification']}", "",
            "| # | Model | Trạng thái | Phủ trọng số | Gate chưa đạt | Điểm | Việc tiếp theo |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranking(store, "verification"), 1):
        out.append(f"| {i} | {r['model']} | **{STATUS_LABEL[r['status']]}** | {r['weight_coverage']}% | {r['gates_open']} | {sc(r)} | {r['next_action'] or ''} |")
    out += [""]
    glabel = {"demand": "Nhu cầu", "supplier": "Nhà cung cấp", "financial": "Tài chính", "identity": "Nhận diện model", "data_confidence": "Data Confidence"}
    gicon = {"pass": "✔ đạt", "insufficient": "◐ chưa đủ", "fail": "✘ trượt"}
    basis = {"observed": "dữ liệu thực tế", "estimated": "ước tính", "ai_judgment": "nhận định AI", "user_input": "anh cung cấp",
             "indirect": "bằng chứng gián tiếp (cấp nhóm sản phẩm)"}
    why_status = {"GO": "mọi Gate đạt, điểm ≥ 75 và dữ liệu phủ ≥ 80% trọng số.",
                  "CONDITIONAL_GO": "không có Gate trượt, dữ liệu nhu cầu và model đủ, nhưng còn điều kiện phải đáp ứng trước khi nhập hàng.",
                  "NEED_MORE_DATA": "Gate Nhu cầu, Nhận diện model hoặc Data Confidence chưa đủ, hoặc ≥ 2 nhóm chưa có dữ liệu. Thiếu dữ liệu không phải là kết luận xấu.",
                  "NO_GO": "có Gate trượt cứng, hoặc điểm thấp khi dữ liệu đã đủ."}
    for r in ranking(store, "verification"):
        e = evs[(r["product_id"], r["variant_key"])]
        m = e.get("model") or {}
        out += [f"## {m.get('display') or e['variant_key']}", "",
                f"Bản ghi `{e['id']}` · `{e['product_id']}` / `{e['variant_key'] or 'chưa xác định'}` · nhóm sản phẩm: {e['product_name']}", "",
                "1. **Sản phẩm và model:** " + (f"thương hiệu {m['brand']}; model {m['model_name']}; giao thức {PROTOCOL_LABEL.get(m['protocol'], m['protocol'])}"
                                                  + (f"; app {m['app_ecosystem']}" if m.get("app_ecosystem") else "")
                                                  + (f"; phiên bản: {', '.join(m['versions'])}" if m.get("versions") else "")
                                                  + (f". **Nhóm nhiều mẫu**: {m.get('group_note')}" if m.get("is_group") else "")
                                                  if m.get("brand") else "chưa có hồ sơ model (thương hiệu, tên, giao thức)"),
                f"2. **Cơ hội:** {e['opportunity']}"]
        for n, g in enumerate(GROUPS, 3):
            x = e["groups"][g]
            s = f"{x['score']}/100" if x["score"] is not None else "Chưa chấm (thiếu dữ liệu)"
            ev = ", ".join(f"`{i}`" for i in x["evidence_ids"][:6]) + (" …" if len(x["evidence_ids"]) > 6 else "")
            extra = ""
            if g == "demand":
                pr, ve = x.get("presence") or {}, x.get("velocity")
                extra = (f" — Presence: {pr.get('listings', 0)} tin, {pr.get('shops', 0)} shop, sàn {', '.join(pr.get('platforms') or []) or 'không'}, "
                         f"nhãn 'đã bán' lũy kế {pr.get('cumulative_sold_label_total', 0)}. Velocity: "
                         + (f"≈ {ve['units_per_30d']} sp/30 ngày trên {ve['listings_measured']} tin ({ve['window']})." if ve else "chưa đo được (cần ≥ 2 lần đo cùng tin đăng)."))
            out.append(f"{n}. **{w['groups'][g]['label']}:** {s} · độ tin cậy {x['confidence']} · {basis.get(x['basis'], x['basis'])}. {x['reason']}{extra}"
                       + (f" Bằng chứng: {ev}" if ev else ""))
        tot = f"{e['total']}" if e["total"] is not None else "Chưa có điểm"
        out += [f"8. **Opportunity Score:** {tot} (phủ {e['weight_coverage']}% trọng số)",
                f"9. **Data Confidence:** {e['data_confidence']} ({conf_lbl(e['data_confidence'])})",
                "10. **Risk Gates:** " + "; ".join(f"{glabel[k]} {gicon[v['status']]} ({v['reason']})" for k, v in e["gates"].items()),
                "11. **Điểm mạnh:** " + ("; ".join(e["strengths"]) or "Chưa có nhóm nào ≥ 70 điểm"),
                "12. **Điểm yếu:** " + ("; ".join(e["weaknesses"]) or "Không có nhóm nào dưới 50 điểm trong các nhóm đã chấm"),
                "13. **Rủi ro chính:** " + ("; ".join(e["risks"]) or "Chưa ghi nhận"),
                "14. **Dữ liệu còn thiếu:** " + ("; ".join(e["missing_data"]) or "Không"),
                f"15. **Trạng thái: {STATUS_LABEL[e['status']]}**, vì {why_status[e['status']]}" + (f" Cụ thể: {'; '.join(e['conditions'])}" if e["conditions"] else ""),
                "16. **Hành động tiếp theo:** " + "; ".join(f"{a['action']} ({a['owner']})" for a in e["next_actions"]),
                "", f"Nhận diện: {e['identity']['match']} bản ghi đúng model, {e['identity']['product_scope']} bản ghi cấp nhóm sản phẩm, "
                f"loại {e['identity']['other_model']} bản ghi model khác, {e['identity']['unassigned']} bản ghi chưa gán model, "
                f"{e['identity']['own_shop']} tin của shop anh.", ""]
    return "\n".join(out)


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
    s.add_argument("--platform", required=True, choices=["shopee", "tiktok_shop", "lazada", "own_site"]); s.add_argument("--variant")
    s.add_argument("--dry", action="store_true", help="Chỉ tính, không ghi")
    s = sub.add_parser("prices", help="Giá bán thị trường (p25/trung vị/p75) từ listing đối thủ"); s.add_argument("--product", required=True)
    s.add_argument("--platform"); s.add_argument("--variant")
    s = sub.add_parser("profit-inputs", help="Ghép đầu vào lợi nhuận từ kho, liệt kê mục còn thiếu")
    s.add_argument("--product", required=True); s.add_argument("--platform", required=True, choices=["shopee", "tiktok_shop", "lazada", "own_site"])
    s.add_argument("--variant")
    s = sub.add_parser("max-cost", help="Giá nhập tối đa để còn lãi ròng theo mục tiêu (%%)"); s.add_argument("--run")
    s.add_argument("--product", required=True); s.add_argument("--platform", required=True, choices=["shopee", "tiktok_shop", "lazada", "own_site"])
    s.add_argument("--inputs", help="JSON hoặc @file; bỏ trống = ghép từ kho (profit-inputs)"); s.add_argument("--targets", default="10,20")
    s.add_argument("--retail", help="id supplier retail_listed (giá lẻ đề xuất của hãng) để tính chiết khấu cần xin")
    s.add_argument("--variant"); s.add_argument("--dry", action="store_true"); s.add_argument("--strict", action="store_true", help="Thiếu chi phí shop thì không tính")
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
    s.add_argument("--variant", help="chỉ dùng chuỗi đã gắn model này (mặc định: chuỗi cấp sản phẩm)")
    s = sub.add_parser("coverage", help="Độ phủ nguồn theo nhu cầu dữ liệu (config/source_fallbacks.json)"); s.add_argument("--product", required=True)
    s = sub.add_parser("pipeline", help="Phase 4: chạy readiness → Agent 03 demand → Agent 04 competitors → Agent 07 evaluate cho từng model, chống ghi trùng, báo cáo tổng hợp")
    s.add_argument("--run", required=True); s.add_argument("--product"); s.add_argument("--variant"); s.add_argument("--all", action="store_true")
    s.add_argument("--out", help="ghi báo cáo tổng hợp Markdown")
    s = sub.add_parser("competitors", help="Agent 04: phân tích đối thủ theo model từ kho (giá theo sàn, tập trung, phàn nàn, khoảng trống); ghi competitor_analysis trừ khi --dry")
    s.add_argument("--run"); s.add_argument("--product"); s.add_argument("--variant"); s.add_argument("--all", action="store_true")
    s.add_argument("--dry", action="store_true"); s.add_argument("--out", help="ghi báo cáo Markdown")
    s = sub.add_parser("remeasure-plan", help="Kế hoạch đo lại nhãn đã bán của cùng tin đăng (Velocity); chỉ đọc, không ghi")
    s.add_argument("--product"); s.add_argument("--variant"); s.add_argument("--today", help="YYYY-MM-DD, mặc định hôm nay")
    s.add_argument("--due-only", action="store_true"); s.add_argument("--out", help="ghi báo cáo Markdown")
    s = sub.add_parser("demand", help="Agent 03: phân tích nhu cầu theo model từ kho (9 câu hỏi, Presence/Velocity, điểm đau...); ghi demand_analysis trừ khi --dry")
    s.add_argument("--run"); s.add_argument("--product"); s.add_argument("--variant"); s.add_argument("--all", action="store_true")
    s.add_argument("--dry", action="store_true"); s.add_argument("--out", help="ghi báo cáo Markdown")
    s = sub.add_parser("evaluate", help="Agent 07: đánh giá theo model (product + variant_key), 5 nhóm v2 + Risk Gates; ghi evaluation trừ khi --dry")
    s.add_argument("--run"); s.add_argument("--product", help="bỏ trống cùng --all để đánh giá mọi sản phẩm"); s.add_argument("--variant")
    s.add_argument("--all", action="store_true", help="mọi sản phẩm, mọi model"); s.add_argument("--judgments", help="JSON/@file nhận định AI cho demand/competition")
    s.add_argument("--dry", action="store_true"); s.add_argument("--force", action="store_true", help="ghi cả khi không có gì thay đổi")
    s = sub.add_parser("eval-diff", help="So sánh 2 bản đánh giá v2 của cùng model"); s.add_argument("--product", required=True)
    s.add_argument("--variant"); s.add_argument("--a"); s.add_argument("--b")
    s = sub.add_parser("ranking", help="score = xếp hạng theo điểm; verification = thứ tự ưu tiên xác minh"); s.add_argument("--by", default="score")
    s = sub.add_parser("readiness", help="Orchestrator bước 3: kiểm tra dữ liệu đầu vào của từng model trước khi đánh giá (không ghi)")
    s.add_argument("--product"); s.add_argument("--variant")
    s = sub.add_parser("eval-report", help="Xuất báo cáo Markdown Agent 07"); s.add_argument("--out", required=True)
    s.add_argument("--title", default="Opportunity Evaluator: đánh giá cơ hội theo model"); s.add_argument("--run")
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
            inp = read_json_arg(a.inputs) if a.inputs != "auto" else build_profit_inputs(st, a.product, a.platform, a.variant)["inputs"]
            cat = (prod.get("platform_categories") or {}).get(a.platform)
            fee_errs = check_fee_refs(inp, amap, a.platform, cat, (biz.get("shop_type") or {}).get(a.platform))
            res = profit_sim(inp, biz, known, set(amap), fee_errs)
            res["platform"] = a.platform
            if a.variant:
                res["variant"] = a.variant
            if not a.dry:
                if not a.run:
                    raise APHError("Cần --run để ghi kết quả (hoặc dùng --dry)")
                res = st.append(a.run, "profit", {"product_id": a.product, "inputs": inp, **res}, validate_kind=False)
        elif a.cmd == "prices":
            st.product(a.product); res = price_stats(st, a.product, a.platform, a.variant)
        elif a.cmd == "profit-inputs":
            st.product(a.product); res = build_profit_inputs(st, a.product, a.platform, a.variant)
        elif a.cmd == "max-cost":
            prod = st.product(a.product)
            amap = {x["id"]: x for x in st.assumptions()}
            biz = cfg("business_profile")
            built = None if a.inputs else build_profit_inputs(st, a.product, a.platform, a.variant)
            inp = read_json_arg(a.inputs) if a.inputs else built["inputs"]
            cat = (prod.get("platform_categories") or {}).get(a.platform)
            fee_errs = check_fee_refs(inp, amap, a.platform, cat, (biz.get("shop_type") or {}).get(a.platform))
            res = max_unit_cost(inp, [int(x) if float(x).is_integer() else float(x) for x in a.targets.split(",")], fee_errs, a.strict)
            res["platform"] = a.platform
            res["variant"] = a.variant
            if a.retail:
                sup = next((r for r in st.records(a.product, kind="supplier") if r["id"] == a.retail), None)
                if not sup or sup.get("price_basis") != "retail_listed" or not sup.get("price_tiers"):
                    raise APHError("--retail phải là supplier retail_listed có giá của sản phẩm này")
                res["discount_needed"] = discount_needed(res, min(sup["price_tiers"], key=lambda x: x.get("min_qty", 1))["price"], a.retail)
            if built:
                res["sources"] = built["sources"]; res["price_stats"] = built["price_stats"]
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
            e = evs[-1]
            res = gate(e) if not str(e.get("weights_version", "")).startswith("v2") else {"status": e["status"], "suggested_research_status": e["suggested_research_status"],
                                                                     "gates": e["gates"], "variant_key": e.get("variant_key")}
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
            res = trend_assess(st, a.product, a.variant)
            if not a.dry:
                if not a.run:
                    raise APHError("trend cần --run (hoặc --dry)")
                if not res["evidence"]:
                    raise APHError("Không có observation chuỗi (series) nào cho sản phẩm này")
                rec_ = {"product_id": a.product, "stage": res["stage"], "basis": res["basis"], "evidence": res["evidence"], "timepoints": res["timepoints"]}
                if a.variant:
                    rec_["variant_key"] = a.variant
                res["record"] = st.append(a.run, "trend", rec_)["id"]
        elif a.cmd == "coverage":
            st.product(a.product); res = coverage(st, a.product)
        elif a.cmd == "duplicates":
            res = duplicates(st)
        elif a.cmd == "evaluate":
            if a.all:
                targets = [(p["product_id"], v) for p in st.products() for v in product_variants(st, p["product_id"])]
            elif a.product:
                targets = [(a.product, a.variant)] if a.variant else [(a.product, v) for v in product_variants(st, a.product)]
            else:
                raise APHError("Cần --product hoặc --all")
            judg = read_json_arg(a.judgments) if a.judgments else None
            if judg and len(targets) != 1:
                raise APHError("--judgments chỉ dùng khi đánh giá đúng một model (--product và --variant)")
            if not a.dry and not a.run:
                raise APHError("Cần --run để ghi đánh giá (hoặc dùng --dry)")
            out = []
            prev = latest_evaluations(st)
            for pid, v in targets:
                ev = evaluate(st, pid, v, judg)
                old = prev.get((pid, v))
                if not a.dry and not a.force and old and same_evaluation(old, ev):
                    out.append({**old, "write": "unchanged"})  # đầu vào, config và kết quả không đổi: không ghi bản trùng
                else:
                    out.append(ev if a.dry else {**st.append(a.run, "evaluation", ev), "write": "new"})
            res = out[0] if len(out) == 1 else [{k: e.get(k) for k in ("id", "product_id", "variant_key", "status", "total", "weight_coverage", "data_confidence", "write")} for e in out]
        elif a.cmd == "pipeline":
            if a.all:
                targets = [(p["product_id"], v) for p in st.products() for v in product_variants(st, p["product_id"])]
            elif a.product:
                targets = [(a.product, a.variant)] if a.variant else [(a.product, v) for v in product_variants(st, a.product)]
            else:
                raise APHError("Cần --product hoặc --all")
            rows = run_pipeline(st, a.run, targets)
            if a.out:
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(render_pipeline_report(rows, a.run), encoding="utf-8")
            res = [{"product_id": r["product_id"], "variant_key": r["variant_key"], "status": r["evaluation"]["status"], "total": r["evaluation"]["total"],
                    "ready_for_go": r["readiness"]["ready_for_go"], "writes": r["writes"], "timings_s": r["timings_s"]} for r in rows]
        elif a.cmd == "remeasure-plan":
            plan = remeasure_plan(st, dt.date.fromisoformat(a.today) if a.today else None, a.product, a.variant)
            if a.due_only:
                plan["rows"] = [r for r in plan["rows"] if r["status"] == "đến hạn"]
            if a.out:
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(render_remeasure_plan(plan), encoding="utf-8")
            res = {k: v for k, v in plan.items() if k != "rows"} | {"rows": [{k: r[k] for k in ("listing", "variant_key", "due", "status", "scored", "warnings")}
                                                                          for r in plan["rows"]]}
        elif a.cmd == "competitors":
            if a.all:
                targets = [(p["product_id"], v) for p in st.products() for v in product_variants(st, p["product_id"])]
            elif a.product:
                targets = [(a.product, a.variant)] if a.variant else [(a.product, v) for v in product_variants(st, a.product)]
            else:
                raise APHError("Cần --product hoặc --all")
            if not a.dry and not a.run:
                raise APHError("Cần --run để ghi phân tích (hoặc dùng --dry)")
            prev, items, out = latest_competitor_analyses(st), [], []
            for pid, v in targets:
                d = competitor_analysis(st, pid, v)
                old = prev.get((pid, v))
                if a.dry:
                    rec, wr = d, "dry"
                elif old and same_demand_analysis(old, d):
                    rec, wr = old, "unchanged"
                else:
                    rec, wr = st.append(a.run, "competitor_analysis", d), "new"
                items.append(rec); out.append({"id": rec.get("id"), "product_id": pid, "variant_key": v, "competitor_score": d["competitor_score"]["score"],
                                               "shops": d["competitor_count"]["shops"], "confidence": d["confidence"], "write": wr})
            if a.out:
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(render_competitor_report(st, items, a.run), encoding="utf-8")
            res = items[0] if (len(items) == 1 and a.dry) else out
        elif a.cmd == "demand":
            if a.all:
                targets = [(p["product_id"], v) for p in st.products() for v in product_variants(st, p["product_id"])]
            elif a.product:
                targets = [(a.product, a.variant)] if a.variant else [(a.product, v) for v in product_variants(st, a.product)]
            else:
                raise APHError("Cần --product hoặc --all")
            if not a.dry and not a.run:
                raise APHError("Cần --run để ghi phân tích (hoặc dùng --dry)")
            prev, items, out = latest_demand_analyses(st), [], []
            for pid, v in targets:
                d = demand_analysis(st, pid, v)
                old = prev.get((pid, v))
                if a.dry:
                    rec, w = d, "dry"
                elif old and same_demand_analysis(old, d):
                    rec, w = old, "unchanged"
                else:
                    rec, w = st.append(a.run, "demand_analysis", d), "new"
                items.append(rec); out.append({"id": rec.get("id"), "product_id": pid, "variant_key": v, "demand_score": d["demand_score"]["score"],
                                               "level": d["demand_score"]["level"], "confidence": d["confidence"], "write": w})
            if a.out:
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(render_demand_report(st, items, a.run), encoding="utf-8")
            res = items[0] if (len(items) == 1 and a.dry) else out
        elif a.cmd == "eval-diff":
            res = eval_diff(st, a.product, a.variant, a.a, a.b)
        elif a.cmd == "ranking":
            res = ranking(st, a.by)
        elif a.cmd == "readiness":
            targets = ([(a.product, a.variant)] if a.variant else [(a.product, v) for v in product_variants(st, a.product)]) if a.product else \
                [(p["product_id"], v) for p in st.products() for v in product_variants(st, p["product_id"])]
            res = [readiness(st, pid, v) for pid, v in targets]
        elif a.cmd == "eval-report":
            Path(a.out).write_text(render_eval_report(st, a.title, a.run), encoding="utf-8")
            res = {"written": a.out, "models": len(ranking(st))}
        elif a.cmd == "reindex":
            res = reindex(st)
    except APHError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
