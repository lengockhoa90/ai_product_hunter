---
name: dtm-profit-analyst
description: Agent 06 Profit Analyst của AI Product Hunter (DTM): tính giá bán thị trường, giá nhập tối đa để còn lãi, và lãi ròng 3 kịch bản trên Shopee/TikTok Shop cho sản phẩm trong kho. Dùng khi nói "chạy Profit Analyst", "tính lãi", "giá nhập tối đa bao nhiêu".
---

# Agent 06 — Profit Analyst

Trả lời 3 câu hỏi cho mỗi sản phẩm và mỗi sàn:
1. Thị trường đang bán giá bao nhiêu?
2. Giá nhập tối đa là bao nhiêu để còn lãi ròng 10% và 20%?
3. Với giá nhập thật thì lãi ròng mỗi sản phẩm, điểm hòa vốn và vốn lô thử là bao nhiêu?

Câu 2 không cần giá nhập: người dùng tự so với bảng giá đại lý của mình. Mọi phép tính chỉ chạy bằng `tools/aph.py`. Không tự cộng trừ, không tự điền số.

## Môi trường chạy
- Cần repo `lengockhoa90/ai_product_hunter` (có `tools/aph.py`) và kho `/mnt/project-files/ai-product-hunter/data`. Thiếu → dừng, báo người dùng chạy trong dự án AI Product Hunter.
- Thu giá bán cần trình duyệt Claude trên PC (`mcp__remote-devices__Claude_Browser__*`). Không có → chỉ tính trên dữ liệu đã có trong kho.
- Đọc trước: `rules/research_rules.md` (mục Lợi nhuận), `rules/scoring_rules.md` (tiêu chí profit), `config/business_profile.json` (vốn thử 10 triệu, shop Shopee thường).

## 1. Đầu vào và nguồn của từng đầu vào
| Đầu vào | Lấy từ | Ghi vào kho bằng |
|---|---|---|
| Giá bán `sell_price` | ≥ 3 tin đăng đối thủ **cùng biến thể** (giao thức, số nút, loại khóa). Thận trọng = phân vị 25, cơ sở = trung vị, lạc quan = phân vị 75 | `add --kind listing` có `price_vnd`, đoạn trích chứa giá |
| Phí sàn | Tổng phí người dùng chốt: `shopee_total_fee_pct_user`, `tiktok_shop_total_fee_pct_user` (27%). `payment_fee_pct` = 0 để không tính trùng | assumption có sẵn |
| Chi phí shop: quảng cáo %, ship shop chịu, đóng gói, vận hành, tỷ lệ hoàn, % thiệt hại khi hoàn, ship nhập, chi phí khác, chi phí cố định lô thử | **Chỉ người dùng cung cấp** | assumption `cost_<trường>` (dùng chung) hoặc `cost_<trường>_<sàn>`, `verification=user_input`, `source_url=user:<mã tin nhắn>`, giá trị là số hoặc `{"conservative":…, "base":…, "optimistic":…}` |
| Giá nhập `unit_cost`, MOQ `moq` | Supplier `price_basis=dealer_quote` (người dùng báo) hoặc `wholesale_listed` (giá sỉ niêm yết) trong nước; chọn giá thấp nhất ở bậc số lượng nhỏ nhất | `add --kind supplier` |

- **Không dùng** giá bán lẻ của hãng (`retail_listed`) hay giá 1688 làm giá nhập.
- Chi phí shop thiếu thì **hỏi người dùng một lần**: liệt kê đủ 9 mục, cho phép trả lời "chưa biết". Không tự đặt mặc định.
- Assumption chi phí shop nằm trong `data/assumptions/assumptions.json`. Thêm hoặc sửa bằng một đoạn Python đọc và ghi lại file JSON, giữ nguyên các mục khác. Ghi `source_date` và `collected_at`.

## 2. Thu giá bán thị trường
1. Lazada: mở trang tag `https://www.lazada.vn/tag/<từ-khóa-không-dấu>/`, đọc thẻ sản phẩm, hoặc JSON `catalog/?ajax=true&q=…` (tối đa 2 từ khóa mỗi lần).
   - 2026-11-24: sau vài lượt gọi, Lazada hiện reCAPTCHA "We need to check if you are a robot". Gặp hộp này → `log-access --status captcha`, dừng, nhờ người dùng tự bấm.
2. Shopee: chỉ khi người dùng có mặt để tự giải CAPTCHA. TikTok Shop: nếu đọc được trang sản phẩm.
3. Mỗi tin đăng ghi một `listing`: `title`, `price_vnd` (giá đang bán, không phải giá gạch), `sold_label`, `url`, đoạn trích nguyên văn chứa giá. Ghi trong `note` nếu biến thể lệch (WiFi và Zigbee, combo nhiều cái).
4. `aph.py prices --product <id>` → cần `status=ok` (≥ 3 tin đăng).

## 3. Tính
1. `aph.py profit-inputs --product <id> --platform shopee`: xem đầu vào đã ghép và danh sách `missing`.
2. `aph.py max-cost --run <run> --product <id> --platform shopee` (mặc định mục tiêu 10% và 20%). Ra giá nhập tối đa cho từng kịch bản. `null` nghĩa là không có giá nhập nào đạt mục tiêu ở giá bán đó.
3. Có giá nhập thật → `aph.py profit --run <run> --product <id> --platform shopee --inputs auto`. Chỉ kết luận khi `status=complete`.
4. Lặp lại cho `tiktok_shop`.
- Kiểm tra `sources.unit_cost`: nếu supplier được chọn lệch biến thể (ví dụ khóa app TTLock khi sản phẩm là khóa Tuya), ghi rõ trong báo cáo và chạy lại với `--inputs @file` sau khi bỏ supplier đó.

## 4. Báo cáo tiếng Việt
Lưu ở `/mnt/project-files/ai-product-hunter/reports/daily/`:
- Bảng giá thị trường: số tin đăng, p25 / trung vị / p75, mã listing.
- Bảng giá nhập tối đa (10% và 20%) theo 3 kịch bản, từng sàn.
- Nếu có giá nhập: lãi ròng mỗi sản phẩm, biên %, điểm hòa vốn, vốn lô thử và có vừa 10 triệu không.
- **Còn thiếu**: các mục trong `missing`, ai cần cung cấp.
- Tách **Dữ liệu quan sát** và **Nhận định của AI**. **Không truy cập được**: mọi `log-access` khác `ok`.
- Kết thúc: `aph.py finish-run …`, `aph.py reindex`.

## Không được
Tự đặt chi phí quảng cáo, hoàn hàng, đóng gói… · dùng giá bán lẻ hãng hay giá 1688 làm giá nhập · lấy giá từ đoạn trích Google · tính tay thay công cụ · kết luận lợi nhuận khi `status=incomplete` · vượt CAPTCHA.
