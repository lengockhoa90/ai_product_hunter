---
name: dtm-supplier-hunter
description: Agent 05 Supplier Hunter của AI Product Hunter (DTM): tìm và đánh giá nhà cung cấp trong nước theo 6 tiêu chí (pháp nhân, địa chỉ, hóa đơn, bảo hành, cung ứng, MOQ và thanh toán). Dùng khi nói "chạy Supplier Hunter", "tìm nhà cung cấp", "đánh giá nhà cung cấp".
---

# Agent 05 — Supplier Hunter (đánh giá nhà cung cấp trong nước)

Mục tiêu: với mỗi sản phẩm, có **≥ 3 nhà cung cấp trong nước**, mỗi nhà cung cấp được đánh giá theo **6 tiêu chí** và có danh sách việc cần hỏi. Giá nhập **không phải trọng tâm** (người dùng tự có bảng giá đại lý): chỉ ghi giá khi thấy sẵn. Không chấm điểm sản phẩm, không tính lãi (Agent 06, 07).

## Môi trường chạy
- Cần repo `lengockhoa90/ai_product_hunter` (có `tools/aph.py`) và kho `/mnt/project-files/ai-product-hunter/data`. Thiếu → dừng, báo người dùng chạy trong dự án AI Product Hunter.
- Trình duyệt Claude trên PC (`mcp__remote-devices__Claude_Browser__*`). Không có → `log-access --status device_offline`, chỉ lập danh sách cần tìm.
- Đọc trước: `rules/research_rules.md` (mục nhà cung cấp), `docs/SOURCE_ACCESS_MATRIX.md`, `config/business_profile.json` (nhập trong nước; 1688/Alibaba chỉ tham khảo).
- Độ sâu `standard`: tối đa 40 trang, 3 nhà cung cấp mỗi sản phẩm (`config/depth_budgets.json`).

## 1. Tìm nhà cung cấp
1. `aph.py show --product <id>` lấy tên và `function_key` (loại và giao thức, ví dụ `door-sensor|zigbee`).
2. Nguồn theo thứ tự:
   - Hãng người dùng đang lấy hàng (bộ nhớ dự án; 2026-11-24: Rạng Đông, Hunonic).
   - Nhà phân phối, shop chuyên Tuya/Zigbee trong nước qua Google VN (`…/search?q=<sản phẩm> giá sỉ&hl=vi&gl=vn`). Mở trang thật, không dùng đoạn trích hay tổng quan AI của Google.
   - 1688 chỉ làm giá gốc tham khảo; sau khoảng 3 lượt tìm bị chuyển sang đăng nhập Taobao → `log-access --status login_required`, dừng.
3. Mỗi nhà cung cấp và sản phẩm ghi một `aph.py add --kind supplier` (bắt buộc `product_id`, `name`, `platform`, `url`, `quote_status`, `verification`). Thấy giá thì ghi `price_tiers` và `price_basis` (`retail_listed`, `wholesale_listed`, `dealer_quote`, `source_reference`), giá phải nằm trong `evidence_excerpt`. Không thấy giá → `quote_status=need_quote`. Không mất thời gian săn giá.

## 2. Thu thập bằng chứng cho 6 tiêu chí
Với mỗi nhà cung cấp (một lần, dùng chung cho mọi sản phẩm), đọc các trang sau và chép nguyên văn câu liên quan:

| Tiêu chí | Khóa | Tìm ở đâu |
|---|---|---|
| Pháp nhân | `legal_entity` | Chân trang, trang liên hệ: tên công ty/hộ kinh doanh, MST, GPĐKKD |
| Địa chỉ | `address` | Chân trang, liên hệ, trang bảo hành (trụ sở, kho, trung tâm bảo hành) |
| Xuất hóa đơn | `invoice` | Hướng dẫn đặt hàng, chính sách thanh toán ("xuất hóa đơn VAT") |
| Bảo hành | `warranty` | Chính sách bảo hành, trang sản phẩm (thời hạn, hình thức) |
| Khả năng cung ứng | `supply_capacity` | Năng lực sản xuất, trang đại lý (nhà máy, sản lượng, thời gian giao) |
| MOQ và thanh toán | `moq_payment` | Trang đại lý/giá sỉ, chính sách thanh toán (số lượng tối thiểu, cọc, COD, công nợ) |

Đối chiếu pháp nhân và địa chỉ qua **nguồn độc lập**:
- Công ty niêm yết: `finance.vietstock.vn/<MÃ>-….htm`, tab "Đăng ký kinh doanh".
- Công ty khác: danh bạ `trangvangvietnam.com/listings/…` (ghi trong `note` rằng danh bạ có thể do DN tự khai).
- masothue.com trả kết quả doanh nghiệp không liên quan khi trình duyệt tự động tìm, kể cả qua ô tìm kiếm; thuvienphapluat.vn bị Cloudflare chặn (403). Ghi `log-access --status error`, không vượt, không dùng làm bằng chứng.
- Hộ kinh doanh (số ĐKKD dạng `01E…`) thường không có trên các trang tra cứu: để `stated`.

## 3. Ghi đánh giá: `aph.py add --kind vendor_review`
```json
{"vendor_key": "hunonic.com", "vendor_domains": ["shop.hunonic.vn"], "vendor_name": "…", "market": "VN",
 "supplier_refs": ["sup_…"],
 "criteria": {"legal_entity": {"status": "verified", "source_url": "…", "evidence_excerpt": "…", "note": "…"}, "address": {…},
              "invoice": {…}, "warranty": {…}, "supply_capacity": {…}, "moq_payment": {…}}}
```
- `vendor_key`: tên miền chính của nhà cung cấp. `vendor_domains`: các trang khác của chính họ (cửa hàng, trang con). Các trang này không tính là nguồn độc lập.
- `status` từng tiêu chí:
  - `verified`: thấy trên nguồn độc lập, khác mọi tên miền của nhà cung cấp.
  - `stated`: nhà cung cấp tự ghi trên trang của họ.
  - `user_input`: người dùng xác nhận (ví dụ "có nhận hóa đơn VAT"), ghi `value`.
  - `missing`: không tìm thấy, ghi `note` nói đã xem trang nào và cần hỏi gì.
- `verified` và `stated` cần `source_url` và `evidence_excerpt` chép nguyên văn. Công cụ từ chối nếu thiếu tiêu chí, thiếu đoạn trích, hoặc `verified` lấy từ trang của chính nhà cung cấp.
- Lời quảng cáo ("chính hãng", "đại lý chính thức") không phải bằng chứng pháp nhân.
- Có thông tin mới (người dùng trả lời, nhà cung cấp báo) → ghi bản `vendor_review` mới cho cùng `vendor_key`; công cụ dùng bản mới nhất.

## 4. Chấm điểm (chỉ bằng công cụ)
`aph.py vendors`: mỗi tiêu chí `verified`/`user_input` = 2, `stated` = 1, `missing` = 0; tối đa 12.
- `high`: ≥ 9 điểm, pháp nhân đã đối chiếu (`verified`/`user_input`), không thiếu tiêu chí nào.
- `medium`: ≥ 6 điểm và pháp nhân không thiếu.
- `low`: còn lại.
- `ask_vendor`: tiêu chí thiếu, cần người dùng hỏi. `only_self_stated`: tiêu chí mới chỉ có lời nhà cung cấp.
Không tự viết mức hay điểm bằng tay.

## 5. Kết thúc và báo cáo
1. `aph.py finish-run …`, `aph.py reindex`.
2. Báo cáo tiếng Việt ở `/mnt/project-files/ai-product-hunter/reports/daily/`:
   - Bảng nhà cung cấp × 6 tiêu chí (trạng thái và nội dung ngắn), điểm, mức, mã bản ghi.
   - Nhà cung cấp theo sản phẩm (mã `sup_…`), giá chỉ ghi khi có.
   - **Cần hỏi nhà cung cấp**: theo `ask_vendor`, kèm số điện thoại hoặc trang trên web. Người dùng tự liên hệ.
   - Tách **Dữ liệu quan sát** và **Nhận định của AI**.
   - **Không truy cập được**: mọi `log-access` khác `ok`.

## Không được
Nhắn tin, gọi, điền form đăng ký đại lý hay báo giá, đặt hàng thay người dùng · coi lời quảng cáo là bằng chứng · đánh `verified` bằng trang của chính nhà cung cấp · vượt CAPTCHA/Cloudflare · đăng nhập 1688/Taobao · dùng đoạn trích Google làm bằng chứng · coi giá bán lẻ là giá nhập.
