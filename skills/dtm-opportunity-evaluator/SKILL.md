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
4. Mỗi model cần **hồ sơ model** (`variant_profile`) để báo cáo ghi đúng hãng, tên model, giao thức, app, phiên bản (không dùng tên nhóm như "Cảm biến cửa WiFi/Zigbee Tuya" cho model Hunonic):
   `aph.py add --run <run> --kind variant_profile --json '{"product_id":..,"variant_key":..,"brand":"Hunonic","model_name":"…","protocol":"wifi|ble|ble_mesh|zigbee|rf|wifi_zigbee|mixed|unknown","app_ecosystem":"…","versions":[…],"evidence":["lis_…"],"evidence_excerpt":"<nguyên văn>","by":"opportunity_evaluator"}'`.
   Công cụ kiểm tra: bằng chứng phải là bản ghi của đúng model, đoạn trích nguyên văn, hãng và giao thức phải xuất hiện trong bằng chứng. Khóa gom nhiều hãng/giao thức → `"is_group":true` + `group_note`; Gate Identity chưa đủ cho tới khi tách model. Giao thức không ghi rõ → `unknown`, không đoán.
5. `aph.py readiness --product <id> [--variant <v>]`: liệt kê đủ / thiếu từng đầu vào và Agent phụ trách, trước khi đánh giá.

## 2. Đánh giá (chỉ bằng công cụ)
- Một model: `aph.py evaluate --run <run> --product <id> --variant <v>`; mọi model của sản phẩm: bỏ `--variant`; toàn kho: `--all`. Xem trước: `--dry`.
- Công cụ tự tính 5 nhóm (0–100), `weight_coverage`, Data Confidence, 5 Gate, trạng thái, rủi ro, việc tiếp theo. Nhóm thiếu dữ liệu = null ("chưa chấm"), không phải 0.
- Nhận định AI (tùy chọn, chỉ Nhu cầu và Cạnh tranh, chỉ một model): `--judgments '{"competition":{"score":..,"reason":"..","evidence":["lis_…"]}}'`. Công cụ chỉ cho lệch ±10 điểm so với điểm tính được, không vượt trần nhóm, evidence phải là bản ghi đầu vào của đúng model; ghi `basis=ai_judgment`. Không dùng để "đẩy" điểm khi thiếu dữ liệu.
- Không tự cộng điểm, không tự đổi trọng số hay ngưỡng trong config.
- Chạy lại cùng dữ liệu không ghi bản ghi trùng (`write: unchanged`); `--force` chỉ khi có lý do.

### Quy tắc v2.1 (config `v2.1`; điểm v1 và v2 cũ vẫn đọc được)
- **Nhu cầu = Presence + Velocity.** Một lần chụp (kể cả nhiều sàn) chỉ là Presence: điểm tối đa 50, Gate Nhu cầu chưa đủ. Velocity chỉ có khi đo lại nhãn "đã bán" của **cùng URL** cách nhau ≥ 7 ngày (quy về sp/30 ngày). Gate đạt khi ≥ 30 sp/30 ngày; trượt khi < 5 sp/30 ngày trên ≥ 3 tin.
- **Xu hướng:** trend có `variant_key` đúng model là trực tiếp; trend cấp sản phẩm áp cho model là **bằng chứng gián tiếp** (độ tin cậy × 0,5).
- **Tài chính:** chỉ đạt khi giá vốn đã xác minh (báo giá đại lý anh Khoa cung cấp, hoặc quan sát được) của đúng model và đủ chi phí thật (không có chi phí ước tính/thiếu). Giá bán lẻ hay giá sỉ niêm yết của NCC không phải giá vốn.

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
2. `aph.py ranking --by score` (Opportunity Score giảm dần) hoặc `--by verification` (thứ tự ưu tiên xác minh, không phải xếp hạng cơ hội); `aph.py eval-report --out /mnt/project-files/ai-product-hunter/reports/daily/<ngày>_opportunity-evaluator_<chủ-đề>.md --run <run>` (2 bảng có tên và tiêu chí riêng + 16 mục mỗi model; mục 15 giải thích vì sao ra trạng thái).
3. Viết phần đầu báo cáo: **Kết luận** (trạng thái từng model, điều gì chặn GO), **Dữ liệu cần anh bổ sung** (từ `next_actions` có owner "Anh Khoa"), **Nhận định của AI** tách riêng, **Giới hạn**.
4. `aph.py finish-run …`, `aph.py reindex`.
5. Việc của Agent khác: chỉ đề xuất theo `next_actions` (Orchestrator quyết định chạy Agent nào), không tự chạy lại toàn bộ.

## Không được
Thu thập lại dữ liệu Agent 01–06 · bịa số liệu, nguồn, chi phí · coi giá bán lẻ là giá nhập · coi trần giá nhập hay lợi nhuận mô phỏng là lợi nhuận thật · gộp dữ liệu khác model/giao thức · chấm điểm cho nhóm không có dữ liệu · sửa hoặc ghi đè bản ghi cũ (dùng bản ghi mới, `retract`) · tự ghi trạng thái nghiên cứu khi người dùng chưa đồng ý.
