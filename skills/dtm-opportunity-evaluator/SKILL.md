---
name: dtm-opportunity-evaluator
description: Agent 07 Opportunity Evaluator của AI Product Hunter (DTM): đánh giá cơ hội theo từng model (5 nhóm, Risk Gates, GO / CONDITIONAL GO / NEED MORE DATA / NO GO) từ dữ liệu Agent 01–06 đã có. Dùng khi nói "chạy Opportunity Evaluator", "đánh giá cơ hội", "xếp hạng sản phẩm", "so sánh đánh giá".
---

# Agent 07 — Opportunity Evaluator

Mục tiêu: cho anh Khoa biết **model nào** đáng đầu tư, model nào cần kiểm chứng thêm, model nào nên bỏ, kèm lý do, bằng chứng và việc tiếp theo.
Agent 07 **chỉ đọc kho**: không thu thập lại dữ liệu Agent 01–06, không tự tạo số liệu, không coi giả định hay trần giá nhập là lợi nhuận thật. Không cần trình duyệt.

## Môi trường
- Repo `lengockhoa90/ai_product_hunter` (`tools/aph.py`), kho `/mnt/project-files/ai-product-hunter/data`. Thiếu → dừng, báo người dùng.
- Đọc trước: `docs/AGENT_07_AUDIT_DESIGN.md` mục 7 (bản thiết kế anh đã duyệt), `config/scoring_weights_v2.json`.

## 1. Chuẩn bị
1. `aph.py new-run --type manual --request "<yêu cầu>" --plan '["opportunity_evaluator"]'`.
2. Đơn vị đánh giá là **product_id + variant_key**. `aph.py show --product <id>` và `aph.py prices --product <id> --variant <v>` để xem model nào có dữ liệu.
3. Bản ghi theo model (tin đăng, NCC, giá, trần giá nhập, lợi nhuận) chưa có `variant_key` thì bị loại. Chỉ gán model khi chính bản ghi ghi rõ model:
   `aph.py add --run <run> --kind variant_link --json '{"product_id":..,"variant_key":..,"target":"sup_…","reason":"…","evidence_excerpt":"<chép nguyên văn từ tên/ghi chú/đoạn trích của bản ghi đích>","by":"opportunity_evaluator"}'`.
   Không chắc (ví dụ không rõ có phải bản cảm ứng) → **không gán**, để Gate Identity báo chưa đủ. Không gán một NCC cho model khác giao thức (WiFi, BLE, Zigbee là model khác nhau).

## 2. Đánh giá (chỉ bằng công cụ)
- Một model: `aph.py evaluate --run <run> --product <id> --variant <v>`; mọi model của sản phẩm: bỏ `--variant`; toàn kho: `--all`. Xem trước: `--dry`.
- Công cụ tự tính 5 nhóm (0–100), `weight_coverage`, Data Confidence, 5 Gate, trạng thái, rủi ro, việc tiếp theo. Nhóm thiếu dữ liệu = null ("chưa chấm"), không phải 0.
- Nhận định AI (tùy chọn, chỉ Nhu cầu và Cạnh tranh, chỉ một model): `--judgments '{"competition":{"score":..,"reason":"..","evidence":["lis_…"]}}'`. Công cụ chỉ cho lệch ±10 điểm so với điểm tính được, không vượt trần nhóm, evidence phải là bản ghi đầu vào của đúng model; ghi `basis=ai_judgment`. Không dùng để "đẩy" điểm khi thiếu dữ liệu.
- Không tự cộng điểm, không tự đổi trọng số hay ngưỡng trong config.

## 3. Đọc kết quả
| Trạng thái | Khi nào |
|---|---|
| NO GO | Gate trượt cứng (nhu cầu yếu đã xác nhận, giá nhập vượt trần, lãi âm), hoặc điểm < 50 khi dữ liệu phủ ≥ 80% |
| NEED MORE DATA | Gate Nhu cầu / Identity / Data Confidence chưa đủ, hoặc ≥ 2 nhóm null |
| GO | Mọi Gate đạt, điểm ≥ 75, phủ trọng số ≥ 80% |
| CONDITIONAL GO | Còn lại, kèm điều kiện |
Xu hướng không phải Gate; `unknown` giữ nguyên là null. Đề xuất `research_status` chỉ là gợi ý: chỉ chạy `set-status` khi người dùng đồng ý.

## 4. So sánh và báo cáo
1. `aph.py eval-diff --product <id> --variant <v>` (2 lần gần nhất, hoặc `--a/--b`): đổi điểm, độ tin cậy, trạng thái, Gate, và bản ghi đầu vào mới / bị rút lại (nguyên nhân).
2. `aph.py ranking`; `aph.py eval-report --out /mnt/project-files/ai-product-hunter/reports/daily/<ngày>_opportunity-evaluator_<chủ-đề>.md --run <run>` (bảng xếp hạng + 16 mục mỗi model).
3. Viết phần đầu báo cáo: **Kết luận** (trạng thái từng model, điều gì chặn GO), **Dữ liệu cần anh bổ sung** (từ `next_actions` có owner "Anh Khoa"), **Nhận định của AI** tách riêng, **Giới hạn**.
4. `aph.py finish-run …`, `aph.py reindex`.
5. Việc của Agent khác: chỉ đề xuất theo `next_actions` (Orchestrator quyết định chạy Agent nào), không tự chạy lại toàn bộ.

## Không được
Thu thập lại dữ liệu Agent 01–06 · bịa số liệu, nguồn, chi phí · coi giá bán lẻ là giá nhập · coi trần giá nhập hay lợi nhuận mô phỏng là lợi nhuận thật · gộp dữ liệu khác model/giao thức · chấm điểm cho nhóm không có dữ liệu · sửa hoặc ghi đè bản ghi cũ (dùng bản ghi mới, `retract`) · tự ghi trạng thái nghiên cứu khi người dùng chưa đồng ý.
