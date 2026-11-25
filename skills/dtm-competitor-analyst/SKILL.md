---
name: dtm-competitor-analyst
description: Agent 04 Competitor Analyst của AI Product Hunter (DTM): phân tích đối thủ của từng model (số shop, phân bố giá theo sàn, mức tập trung, tính năng, phàn nàn, lợi thế, khoảng trống). Dùng khi nói "chạy Competitor Analyst", "phân tích đối thủ", "ai đang bán model này", "khoảng trống thị trường".
---

# Agent 04 — Competitor Analyst

Mục tiêu: cho anh Khoa biết với **đúng model** này, ai đang bán, giá phân bố ra sao trên từng sàn, thị trường có bị vài shop chiếm không, khách chê gì ở hàng đối thủ và còn khoảng trống nào. Không khái quát từ vài đánh giá.

## Môi trường
Repo `lengockhoa90/ai_product_hunter`, kho `/mnt/project-files/ai-product-hunter/data`. Thu thập chỉ trong thread có PC liên kết. Lịch cloud: chỉ chạy bước 3 trên dữ liệu đã có.

## 1. Chuẩn bị
`aph.py new-run --type manual --request "<yêu cầu>" --plan '["competitor_analyst"]'`, rồi `aph.py competitors --product <id> --dry` để xem `missing_data`.

## 2. Thu thập (chỉ phần thiếu)
- **Tin đăng đúng model** trên Lazada, Shopee, TikTok Shop, Tiki: ghi `listing` có `variant_key`, `shop_name`, `price_vnd`, `sold_label`, `evidence_excerpt` nguyên văn (cách làm giống Agent 06). Kiểm tra mã model đầy đủ: cùng tiền tố nhưng khác hậu tố có thể là sản phẩm khác (ví dụ Rạng Đông `CTCU.WF CN.02T` là công tắc cảm ứng, `CTCU.WF CN.DOT` là công tắc cửa cuốn; Hunonic `Datic DTSW03` là mặt cảm ứng 3 nút, `Datic DTBS-01` là module nhỏ). Không chắc → không gán model.
- **Điểm yếu đối thủ:** ghi vào `weaknesses` của listing khi chính trang ghi ra; phàn nàn của người mua ghi bằng `demand` theo skill `dtm-demand-analyst` (`aspect`, `mentions`, `sample_size`).
- Bỏ shop của anh Khoa ("Dien Toan My") khỏi đối thủ (công cụ tự loại).
- CAPTCHA / xác minh lưu lượng (Shopee `verify/traffic`, Lazada baxia): dừng, `log-access --status captcha`, nhờ anh Khoa xử lý.

## 3. Phân tích (chỉ bằng công cụ)
`aph.py competitors --run <run> --product <id> [--variant <v>] | --all [--out <báo cáo.md>]`:
- Điểm cạnh tranh = nhóm Cạnh tranh của Agent 07 (cùng quy tắc; nhiều shop hơn là khó hơn).
- Đối thủ (shop, loại hãng/nhà phân phối hay shop thường, số tin, nhãn bán lũy kế, giá thấp nhất).
- Phân bố giá theo sàn (min/P25/trung vị/P75/max), chỉ khi ≥ 3 tin trên sàn đó.
- Mức tập trung: tỷ trọng shop đầu và HHI theo nhãn "đã bán" lũy kế (cần ≥ 2 shop có nhãn); đây không phải thị phần hiện tại.
- Phàn nàn, tính năng được khen, lợi thế, khoảng trống: khoảng trống "có cơ sở" chỉ khi ≥ 3 lượt nhắc trên ≥ 2 tin; còn lại là "giả thuyết".
- Chạy lại cùng dữ liệu → `unchanged`. `competitor_analysis` không là đầu vào của Agent 07.

## 4. Báo cáo
`--out /mnt/project-files/ai-product-hunter/reports/daily/<ngày>_competitor-analyst_<chủ-đề>.md`, thêm phần đầu (kết luận theo model, dữ liệu mới, không truy cập được, việc cần anh Khoa), rồi `finish-run`, `reindex`.

## Không được
Bịa đối thủ, giá, nhãn bán · gộp sản phẩm khác mã model · khái quát phàn nàn từ mẫu nhỏ · coi nhãn lũy kế là thị phần · vượt CAPTCHA · nhắn tin cho shop đối thủ.

## Giai đoạn 5 (25/11/2026): quy tắc bổ sung
- Đọc `competitor_state` trước khi kết luận: `not_collected` (số shop UNKNOWN, không phải 0), `searched_none_found` (đã ghi observation `search_result_count` = 0), `insufficient` (< 3 tin), `verified`. Không nêu lợi thế cạnh tranh khi chưa `verified`.
- Khi đã tìm mà không thấy tin nào của model, ghi `observation` metric `search_result_count`, value 0, `variant_key`, `source_url` trang tìm kiếm.
- Tin bán nhiều phân loại (1–4 nút…): ghi `sku_scope: "multi_sku"` trên listing (hoặc `variant_link` có `sku_scope` cho tin cũ). Tin này tính là shop đối thủ nhưng không vào trung vị giá hay nhãn bán của biến thể.
- Hàng thay thế khác hãng cùng chức năng: ghi listing cấp sản phẩm (không `variant_key`) với `substitute_for: <variant_key>`, `brand`. Chỉ để thấy mặt bằng giá, không gộp vào đối thủ cùng model.
- Shopee: chỉ đọc thẻ trang tìm kiếm; trang chi tiết sản phẩm không hiển thị khi mở tự động, không gọi API.
