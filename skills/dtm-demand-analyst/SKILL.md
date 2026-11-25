---
name: dtm-demand-analyst
description: Agent 03 Demand Analyst của AI Product Hunter (DTM): phân tích nhu cầu thật của từng model (tìm kiếm, tương tác, bán hàng, đánh giá; Presence/Velocity; điểm đau, lý do mua/không mua, giá chấp nhận, mùa vụ, khác biệt nền tảng). Dùng khi nói "chạy Demand Analyst", "phân tích nhu cầu", "khách chê gì", "có mùa vụ không".
---

# Agent 03 — Demand Analyst

Mục tiêu: trả lời 9 câu hỏi về nhu cầu của **từng model** bằng dữ liệu có nguồn: có nhu cầu thật không · tăng hay giảm · từ khóa · tính năng quan tâm · vì sao mua · vì sao không mua/trả hàng · giá chấp nhận · mùa vụ · khác biệt nền tảng. Câu nào thiếu dữ liệu giữ **UNKNOWN**.

## Môi trường
Repo `lengockhoa90/ai_product_hunter`, kho `/mnt/project-files/ai-product-hunter/data`. Thu thập chỉ trong thread có PC liên kết (trình duyệt `mcp__remote-devices__Claude_Browser__*`). Lịch cloud: chỉ chạy bước 3–4 trên dữ liệu đã có.

## 1. Chuẩn bị
1. `aph.py new-run --type manual --request "<yêu cầu>" --plan '["demand_analyst"]'`.
2. `aph.py demand --product <id> --dry` (hoặc `--variant`): xem câu nào UNKNOWN và `missing_data`. Chỉ thu thập phần còn thiếu.

## 2. Thu thập (tách 4 loại nhu cầu, không trộn)
| Loại | Nguồn | Ghi |
|---|---|---|
| Tìm kiếm | Google Trends (chuỗi 12 tháng/5 năm, cụm liên quan "Hàng đầu"), TikTok Search/CSI, gợi ý tìm kiếm Shopee/TikTok | `observation` metric `search_interest` (kèm `series`), `csi_popularity`, `search_keyword` (value = từ khóa) |
| Tương tác | Lượt xem/thích video, bài viết | `observation` metric `content_signal`/`video_views` — chỉ là chú ý, **không** phải người mua |
| Bán hàng | Nhãn "đã bán" của **cùng URL** tin đăng, đo lại sau ≥ 7 ngày | `listing` (cùng `url`, có `observed_at`, `variant_key`) → Velocity |
| Đánh giá | Nội dung đánh giá Lazada/Tiki/Shopee/TikTok Shop, hỏi đáp, diễn đàn | `observation` metric `review_count` (đoạn trích "Đánh giá(21)") + mỗi chủ đề một `demand` |

Bản ghi `demand` cho chủ đề đánh giá: `type` (complaint, purchase_intent, product_demand, price_sensitivity, question…), `aspect` (`pain_point`, `buying_reason`, `non_buy_reason`, `return_reason`, `feature`, `use_case`, `question`, `price`, `alternative`), `theme`, `mentions` (số đánh giá nhắc tới), `sample_size` (số đánh giá đã đọc), `platform`, `variant_key`, `evidence_excerpt` (nguyên văn, nối bằng " | "), `evidence` (id observation).
- Chỉ ghi điều người mua **thực sự viết**. Ý kiến "giá hơi cao" trong đánh giá 5 sao là `price`, không phải `non_buy_reason`.
- Lời khen dịch vụ shop (giao nhanh, đóng gói) không phải lý do mua sản phẩm.
- Đánh giá của model khác giao thức/hãng → `variant_key` của model đó.
- CAPTCHA (Lazada hộp baxia, Shopee): dừng, `log-access --status captcha`, nhờ anh Khoa giải; không thử lại liên tục. Google Trends trả lỗi tần suất → dừng, ghi `log-access --status blocked`.

## 3. Phân tích (chỉ bằng công cụ)
`aph.py demand --run <run> --product <id> [--variant <v>] | --all [--out <báo cáo.md>]`. Công cụ tự tính:
- Demand Score, Presence, Velocity (cùng quy tắc Agent 07 v2.1), Gate Nhu cầu.
- Điểm đau / lý do mua / lý do không mua / tính năng: gom theo chủ đề, ghi `mentions/sample_size`; mẫu < 30 đánh giá hoặc < 3 lượt nhắc → "mẫu nhỏ, không khái quát".
- Giá chấp nhận: khoảng giá của tin có nhãn "đã bán" + ý kiến về giá; không suy ra giá tối ưu.
- Mùa vụ: cần chuỗi ≥ 350 ngày; 1 năm = chỉ báo, chuỗi cấp nhóm sản phẩm = gián tiếp.
- Khác biệt nền tảng: số tin, shop, giá trung vị, nhãn bán, Velocity theo sàn.
- Chạy lại cùng dữ liệu → `unchanged`, không ghi trùng. Bản ghi `demand_analysis` không phải đầu vào của Agent 07 (Agent 07 tự đọc dữ liệu gốc).

## 4. Báo cáo
`--out /mnt/project-files/ai-product-hunter/reports/daily/<ngày>_demand-analyst_<chủ-đề>.md`, thêm phần đầu: kết luận theo model, dữ liệu đã thu (nguồn, ngày), "Không truy cập được", việc cần anh Khoa. Rồi `finish-run`, `reindex`.

## Không được
Bịa số đánh giá, chủ đề hay đoạn trích · khái quát từ vài đánh giá · coi lượt xem là nhu cầu mua · coi nhãn "đã bán" lũy kế là tốc độ bán · gộp đánh giá khác model · vượt CAPTCHA.

## Giai đoạn 5 (25/11/2026): đo lại cho Velocity
- Từ 01/12/2026: `python3 tools/aph.py remeasure-plan --due-only --out <file>` để lấy danh sách tin đến hạn (chỉ đọc, không ghi).
- Đo lại đúng tin cũ (cùng sàn + mã tin), ghi listing mới cùng URL với `observed_at` = ngày đo và nhãn nguyên văn. Không ghi gì cho tin chưa đến hạn.
- Nhãn làm tròn ('2k+', '1,2k') không cho ra tốc độ; chỉ nhãn số cụ thể ở cả 2 lần đo mới tính Velocity.
- Shopee: tìm lại đúng mã `shop.item` trên trang tìm kiếm; Lazada: `catalog/?ajax=true` tìm đúng `pdp-i<mã>`.
