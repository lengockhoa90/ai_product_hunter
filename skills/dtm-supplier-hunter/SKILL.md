---
name: dtm-supplier-hunter
description: Agent 05 Supplier Hunter của AI Product Hunter (DTM): tìm nhà cung cấp trong nước và giá nhập cho sản phẩm trong kho, tách giá bán lẻ với giá sỉ. Dùng khi nói "chạy Supplier Hunter", "tìm nhà cung cấp", "giá nhập bao nhiêu".
---

# Agent 05 — Supplier Hunter (nhà cung cấp trong nước)

Mục tiêu: với mỗi sản phẩm, có **≥ 3 nhà cung cấp trong nước** kèm giá, hoặc ghi rõ "cần báo giá". Không chấm điểm, không tính lãi (đó là Agent 06, 07).

## Môi trường chạy
- Cần repo `lengockhoa90/ai_product_hunter` (có `tools/aph.py`) và kho `/mnt/project-files/ai-product-hunter/data`. Thiếu → dừng, báo người dùng chạy trong dự án AI Product Hunter.
- Trình duyệt Claude trên PC (`mcp__remote-devices__Claude_Browser__*`). Không có → `log-access --status device_offline`, chỉ lập danh sách cần tìm.
- Đọc trước: `rules/research_rules.md` (mục nhà cung cấp), `docs/SOURCE_ACCESS_MATRIX.md`, `config/business_profile.json` (nhập trong nước; 1688/Alibaba chỉ tham khảo).
- Độ sâu `standard`: tối đa 40 trang, 3 nhà cung cấp mỗi sản phẩm (`config/depth_budgets.json`).

## 1. Chọn sản phẩm
`aph.py show --product <id>` lấy tên, `function_key` (loại và giao thức, ví dụ `door-sensor|zigbee`). Ưu tiên sản phẩm Agent 02 cho là đang tăng hoặc người dùng chỉ định.

## 2. Nguồn, theo thứ tự
1. **Hãng người dùng đang lấy hàng** (xem bộ nhớ dự án; 2026-11-24: Rạng Đông, Hunonic). Đọc trang chính thức:
   - hunonic.com: tìm `https://hunonic.com/?s=<từ khóa>&post_type=product`, đọc thẻ sản phẩm (giá hiện tại). Giá trên web là giá bán lẻ.
   - rangdong.com.vn: vào danh mục (`/category/cam-bien-thong-minh`, `/category/cong-tac-thong-minh`), mở trang sản phẩm, đọc dòng "Giá bán lẻ đề xuất". Tìm kiếm đúng mẫu là `/search.html?key=…`.
   - Giá đại lý của hãng không công khai → `quote_status=need_quote`, nhắc người dùng dùng bảng giá đại lý của chính họ.
2. **Nhà phân phối, shop chuyên Tuya/Zigbee trong nước** qua Google VN (`…/search?q=<sản phẩm> giá sỉ&hl=vi&gl=vn`): ví dụ tuya.vn (có ĐKKD ở chân trang), tuyaviet.com, trang "bảng giá sỉ" cho thợ. Mở trang thật, không dùng đoạn trích hay tổng quan AI của Google.
3. **1688 làm giá gốc tham khảo**: từ khóa mã hóa GBK. Khoảng 3 lượt tìm liên tiếp thì bị chuyển sang đăng nhập Taobao → `log-access --status login_required`, dừng, không đăng nhập.

## 3. Ghi kho: `aph.py add --kind supplier`
Bắt buộc: `product_id`, `name`, `platform`, `url` (trang đã mở), `quote_status`, `verification`.
- `price_basis` (bắt buộc khi có `price_tiers`):
  - `retail_listed`: giá bán lẻ trên web hãng/shop, **không phải giá nhập**
  - `wholesale_listed`: trang ghi rõ là giá sỉ/giá thợ
  - `dealer_quote`: báo giá đại lý (người dùng cung cấp, `verification=user_input`)
  - `source_reference`: giá 1688/Alibaba, `market=CN`
- `price_tiers`: `[{"min_qty":1,"price":số,"currency":"VND"}]`. Với `verification=observed`, mọi giá phải nằm trong `evidence_excerpt` chép nguyên văn (công cụ kiểm tra).
- `quote_status`: `listed` hoặc `quoted` chỉ khi có giá; chưa có giá nhập thật thì dùng `need_quote` (vẫn ghi giá lẻ để tham khảo).
- `trust_level` (`low`, `medium`, `high`) và `trust_signals`: chỉ dùng tín hiệu kiểm chứng được, như mã số thuế/ĐKKD, địa chỉ, năm hoạt động, việc người dùng đã mua. Lời quảng cáo ("chính hãng", "đền gấp 10") không tính.
- `note`: ghi lệch biến thể (WiFi/BLE và Zigbee, app TTLock và Tuya).

## 4. Kiểm tra trước khi kết thúc
- `aph.py coverage --product <id>`, đọc `needs.supply`: `cost_price_known` (đã có giá sỉ hoặc báo giá trong nước chưa), `need_quote` (danh sách cần anh hỏi giá).
- `aph.py finish-run …`, `aph.py reindex`.

## 5. Báo cáo tiếng Việt
Lưu ở `/mnt/project-files/ai-product-hunter/reports/daily/`:
- Bảng theo sản phẩm: nhà cung cấp, model, giao thức, giá, loại giá, bảo hành, độ tin cậy, mã bản ghi.
- **Cần báo giá**: danh sách để người dùng tự liên hệ, kèm số điện thoại hoặc trang trên web.
- Tách **Dữ liệu quan sát** và **Nhận định của AI**.
- **Không truy cập được**: mọi `log-access` khác `ok`.

## Không được
Nhắn tin, gọi, điền form báo giá, đặt hàng, thêm giỏ thay người dùng · coi giá bán lẻ là giá nhập · coi 1688 là nhà cung cấp · đăng nhập 1688/Taobao · dùng đoạn trích Google làm bằng chứng.
