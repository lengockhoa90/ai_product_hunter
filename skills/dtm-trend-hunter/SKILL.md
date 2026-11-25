---
name: dtm-trend-hunter
description: Agent 02 Trend Hunter của AI Product Hunter (DTM): kiểm tra sản phẩm trong kho có đang tăng trưởng ở VN không bằng Google Trends và nguồn thứ hai. Dùng khi nói "chạy Trend Hunter", "kiểm tra xu hướng", "sản phẩm này có đang tăng không".
---

# Agent 02 — Trend Hunter (xu hướng sản phẩm)

Khác skill `trend-hunter-dtm` (chủ đề video). Agent này chỉ trả lời: **sản phẩm trong kho đang ở giai đoạn nào** (emerging, growing, trending, stable, declining, unknown). Không kết luận nhu cầu: tìm kiếm và lượt xem chỉ là tín hiệu chú ý.

## Môi trường chạy
- Cần repo `lengockhoa90/ai_product_hunter` (có `tools/aph.py`) và kho `/mnt/project-files/ai-product-hunter/data`. Thiếu một trong hai → dừng, báo người dùng chạy trong dự án AI Product Hunter.
- Trình duyệt Claude trên PC (`mcp__remote-devices__Claude_Browser__*`). Không có → `log-access --status device_offline`, chỉ ghi kế hoạch từ khóa, không kết luận.
- Đọc trước: `rules/research_rules.md` mục 3 (xu hướng cần ≥ 2 mốc và ≥ 2 nguồn độc lập), `docs/SOURCE_ACCESS_MATRIX.md`.

## 1. Chọn sản phẩm và từ khóa
1. `aph.py list` hoặc danh sách từ run của Agent 01. Mỗi sản phẩm chọn **một từ khóa tiếng Việt người mua hay gõ** (ngắn, không thương hiệu, ví dụ "công tắc thông minh", "cảm biến cửa").
2. Ghi rõ trong `note` khi từ khóa rộng hơn hoặc lệch sản phẩm (ví dụ "giàn phơi thông minh" ở VN chủ yếu là loại quay tay; "zigbee" chỉ đại diện gián tiếp cho gateway).
3. Gom 5 từ khóa một lượt so sánh. Đặt **một từ khóa neo chung** (ví dụ "công tắc thông minh") vào mọi lượt để so được giữa các lượt.

## 2. Google Trends (nguồn 1)
1. Mở `https://trends.google.com/trends/explore?geo=VN&date=today%2012-m&q=<k1>,<k2>,…&hl=vi` (từ khóa mã hóa URL, cách nhau bằng dấu phẩy).
2. Đợi 8 giây, đọc bảng theo tuần bằng `javascript_tool`:
   `document.body.innerText.replace(/[\u202a-\u202e]/g,'').split('\n').filter(l=>/^\d{1,2} thg \d+, \d{4}\t/.test(l))`
   Mỗi dòng: `ngày<TAB>y1<TAB>…<TAB>y5` theo đúng thứ tự từ khóa trong URL. Đủ 52–53 dòng mới dùng.
3. Lưu bảng thô vào thư mục tạm trước khi ghi kho.
4. Mỗi từ khóa ghi một `observation`: `metric=search_interest`, `platform=google_trends`, `source_type=trends`, `verification=observed`, `confidence=0.8`, `value` = giá trị tuần cuối, `evidence_excerpt` = nguyên dòng tuần cuối, `observed_at` = ngày tuần cuối (ISO), `series` = `[{"t":"YYYY-MM-DD","v":số}, …]` đủ 12 tháng, `note` = từ khóa, cột yN, các từ khóa cùng lượt, cảnh báo lệch nghĩa.
5. Trang báo "phiên bản cũ sắp ngừng": vẫn dùng được (2026-11-24); khi trang cũ ngừng, thử trang mới và cập nhật skill.

## 3. Nguồn thứ 2 (bắt buộc để kết luận)
Theo thứ tự, dùng nguồn đầu tiên mở được:
- TikTok Creator Search Insights `https://www.tiktok.com/csi` (người dùng tự đăng nhập):
  1. Mở `https://www.tiktok.com/csi/search?keyword=<từ khóa>`, đợi 5 giây, lấy danh sách chủ đề từ `document.body.innerText` (phần sau "Hành động nhanh", tách theo "Tải lên").
  2. Chỉ dùng chủ đề **trùng hoặc rất gần** từ khóa (ghi rõ trong `note` khi lệch). Không có chủ đề khớp → ghi trong báo cáo, không thay bằng chủ đề khác nghĩa. Chủ đề tiếng nước ngoài không dùng.
  3. Bấm vào chủ đề, chọn "6 tháng qua" → Xác nhận. Đọc chuỗi theo ngày từ phản hồi mạng `inspired_query/detail/popularity` bằng `read_network_requests` (không đọc số từ hình biểu đồ).
  4. Cộng theo khối 7 ngày kết thúc ở ngày cuối. Khoảng 0 liên tục trong chủ đề vốn có số (đã gặp 18/6–27/7/2026) là mất dữ liệu: tuần chạm khoảng đó ghi `v: null`, không ghi 0.
  5. `observation`: `metric=csi_popularity`, `platform=tiktok_csi`, `source_type=social`, `value` = độ phổ biến 7 ngày hiển thị, `evidence_excerpt` = dòng chủ đề trên trang tìm kiếm (ví dụ "Đèn cảm biến 399"), `series` = các tuần. Công cụ tự quy chuỗi số tuyệt đối về 0–100.
- TikTok tìm kiếm video `tiktok.com/search/video?q=…`: chỉ khoảng 20 video xếp theo liên quan, không đủ làm chuỗi theo tuần. Chỉ dùng để xem nội dung, không làm nguồn xu hướng.
- Nhãn "Đã bán" cùng tin đăng Lazada/Shopee đo ở **hai ngày khác nhau** → `metric=sold_label` (đây là tín hiệu nhu cầu, quý hơn tín hiệu chú ý).
Trình duyệt từ chối mở tiktok.com → `log-access --status error`, không thử lách; báo người dùng.

## 4. Phân loại (chỉ bằng công cụ)
`aph.py trend --run <run> --product <id>` (thêm `--dry` để xem trước).
- Công cụ so **trung vị** 12 tuần gần nhất với 12 tuần trước đó (một tuần đột biến không đổi kết quả).
- Trung vị cả hai nửa dưới 10, hoặc hơn nửa số tuần bằng 0 → `low_volume` (quá ít người tìm để đọc xu hướng).
- Tăng ≥ 30%: `emerging` (đầu năm gần như 0), `trending` (gấp ≥ 1,5 lần trung vị năm), còn lại `growing`. Giảm ≥ 30%: `declining`. Còn lại `stable`.
- **Chỉ kết luận khi ≥ 2 nguồn khác nhau có tín hiệu và cùng hướng.** Cùng hướng nhưng khác mức (growing và trending) → công cụ chọn mức thận trọng (growing). Một nguồn → `stage=unknown`, `basis` ghi chỉ báo của nguồn đó. Hai nguồn ngược hướng → `unknown`, ghi "mâu thuẫn".
- Không tự viết `stage` bằng tay. Phân loại sai do công cụ → sửa công cụ, chạy kiểm thử, `aph.py retract` bản ghi cũ rồi chạy lại.

## 5. Kết thúc và báo cáo
1. `aph.py finish-run …`, `aph.py reindex`.
2. Báo cáo tiếng Việt, lưu ở `/mnt/project-files/ai-product-hunter/reports/daily/`:
   - Bảng: sản phẩm, từ khóa, trung vị 12 tuần gần/trước, % thay đổi, chỉ báo Trends, giai đoạn kết luận, mã bằng chứng.
   - Tách **Dữ liệu quan sát** và **Nhận định của AI** (ví dụ mùa vụ, đột biến một tuần).
   - **Không truy cập được**: mọi `log-access` khác `ok`.
   - Nhắc: Trends là chỉ số tương đối trong từng lượt so sánh, không phải số lượt tìm.

## Không được
Kết luận xu hướng từ một nguồn hay một mốc · dùng lượt xem/tìm kiếm làm bằng chứng nhu cầu · đọc số từ hình biểu đồ thay vì bảng · dùng tổng quan AI của Google · vượt CAPTCHA hay đăng nhập thay người dùng.

## Giai đoạn 5 (25/11/2026): từ khóa và phạm vi
- Chuỗi cấp thương hiệu/danh mục ghi ở cấp sản phẩm (không `variant_key`), ghi rõ phạm vi trong `note`. Chỉ gắn `variant_key` khi từ khóa là tên riêng của model.
- `aph.py trend --product <id> --variant <key>` chỉ dùng chuỗi gắn model; mặc định chỉ dùng chuỗi cấp sản phẩm (không trộn).
- Luôn xem cụm truy vấn liên quan trước khi dùng một từ khóa. Từ khóa nhiễu đã kiểm chứng ghi vào `config/noisy_series.json` (ví dụ 'công tắc thông minh' lẫn câu hỏi thi công vụ, 'cảm biến cửa' lẫn cửa tự động).
