# SOURCE ACCESS MATRIX

> Kiểm thử thật ngày 2026-11-24 (giờ VN ~10:30). Từ khóa thử: "cảm biến rò rỉ nước" (vi), "zigbee water leak sensor" (en), "漏水传感器" (zh, mã GBK).
> Kiểm thử lại khi một Agent báo lỗi truy cập liên tục, hoặc mỗi 30 ngày.

## Kênh truy cập

| Kênh | Kết quả |
|---|---|
| Container cloud (curl trực tiếp) | Bị proxy chặn (HTTP 403) tới shopee.vn, tiktok.com, google.com, trends.google.com, 1688.com, alibaba.com |
| WebSearch (công cụ Claude) | Hoạt động, chỉ mục thiên về quốc tế, ít trang VN |
| Trình duyệt Claude trên PC người dùng | Hoạt động khi PC bật và app Claude desktop đang mở. Đọc trang bằng `get_page_text` hoặc `javascript_tool` (đợi 3–5 giây cho trang render) |

## Kết quả từng nguồn (qua trình duyệt Claude)

| Nguồn | URL mẫu | Kết quả | Dữ liệu đọc được | Agent dùng |
|---|---|---|---|---|
| Shopee tìm kiếm (đã đăng nhập) | `shopee.vn/search?keyword=…` | ❌ **CAPTCHA** (`/verify/captcha`, trước đó `/verify/traffic`). Không được tự vượt | — | 04, 03 (chỉ khi người dùng tự giải CAPTCHA trong khung trình duyệt) |
| TikTok tìm kiếm video | `tiktok.com/search/video?q=…` | ✅ (trang `/search?q=` lần đầu báo lỗi máy chủ; `/search/video` chạy) | Mô tả video, tên kênh, ngày đăng, số lượt thích | 02, 03 |
| TikTok Creator Search Insights | `tiktok.com/csi` | ✅ (đã đăng nhập) | Chủ đề tìm kiếm, độ phổ biến, % tăng | 02, 01 |
| Google Trends (Khám phá cũ) | `trends.google.com/trends/explore?geo=VN&date=today%2012-m&q=a,b&hl=vi` | ✅ | **Bảng số theo tuần** (đọc từ `document.body.innerText`), tiểu vùng, cụm từ liên quan. Trang báo "phiên bản cũ sắp ngừng" → cần phương án trang mới | 02 |
| Google Search VN | `google.com/search?q=…&hl=vi&gl=vn` | ✅ | Kết quả web có giá, tên đơn vị bán. **Phần "Thông tin tổng quan do AI tạo" không được dùng làm nguồn**; chỉ dùng kết quả web có URL và phải mở trang gốc để xác minh | 01, 04, 05 |
| Lazada VN | `lazada.vn/catalog/?q=…` | ✅ | Tổng số sản phẩm ("Tìm thấy 4293 sản phẩm"), giá, % giảm, "Đã bán", số đánh giá, nơi bán | 04, 03 |
| Alibaba.com | `alibaba.com/trade/search?SearchText=…` | ✅ (không đăng nhập) | Tên SP, giá (hiển thị VND), MOQ, số đã bán, tên NCC, số năm, quốc gia, điểm đánh giá | 05 (tham khảo), 01 |
| 1688 | `s.1688.com/selloffer/offer_search.htm?keywords=<GBK>` | ✅ (không đăng nhập), từ khóa **bắt buộc mã hóa GBK** (UTF-8 → 0 kết quả) | Tên SP, giá ¥, số đã bán, tỷ lệ khách quay lại, tên NCC | 05 (tham khảo giá gốc), 01 |
| Website nhà phân phối VN (ví dụ tuyasmart.com.vn, eitsmart.com.vn, tuyaviet.com) | qua Google | ✅ (thấy trên kết quả Google; mở từng trang khi nghiên cứu) | Giá niêm yết, tình trạng hàng, địa chỉ | 05 (nguồn chính, theo D3), 04 |
| Lazada trang tag | `lazada.vn/tag/<tu-khoa>/` | ✅ (2026-11-24 12:20) | Từng tin đăng: giá, % giảm, "Đã bán", số đánh giá, nơi bán | 01, 04 |
| Lazada đánh giá sản phẩm | trang `pdp-i…html`, cuộn xuống | ✅ | Nội dung đánh giá, ngày. Nhiều đánh giá có nhãn "từ các nền tảng khác" (dịch) → `reported`. Sản phẩm ít bán không có mục hỏi đáp | 01, 03 |
| otofun.net.vn | chủ đề qua liên kết Google | ✅ | Nội dung bài và trả lời, ngày đăng | 01, 03 |
| tinhte.vn tìm kiếm | `tinhte.vn/search/?q=…` | ⚠️ Chỉ hiện form, không có kết quả qua URL. Tìm qua Google | — | 01, 03 |
| voz.vn | `voz.vn/search/…` | ❌ Cloudflare "Just a moment…" (HTTP 403). Không vượt | — | — |
| Facebook nhóm/bài | qua Google | ❌ Phần lớn nội dung cần đăng nhập. Không dùng đoạn trích Google | — | — |
| TikTok (lần 2) | `tiktok.com/csi` | ❌ 2026-11-24 13:00: trình duyệt Claude từ chối mở tiktok.com (navigation denied). Lần trước (10:30) mở được | — | 02 |
| Taobao, Tiki, TikTok Shop (web), Amazon | — | ⬜ Chưa kiểm thử | — | — |

## Hệ quả cho thiết kế

1. **Shopee**: không thu thập tự động. Agent 04 dùng Lazada + Google + TikTok làm tín hiệu cạnh tranh VN; dữ liệu Shopee chỉ lấy khi người dùng tự giải CAPTCHA, hoặc người dùng dán/xuất dữ liệu (ví dụ từ Seller Center). Mọi lần gặp CAPTCHA ghi `log-access --status captcha`.
2. **Nguồn cung trong nước** (D3): Google VN → website nhà phân phối/nhà sản xuất VN là nguồn chính. 1688/Alibaba chỉ dùng để ước lượng giá gốc và phát hiện sản phẩm.
3. **Xu hướng**: Google Trends cho bảng số theo tuần (đủ ≥ 2 mốc thời gian). TikTok CSI cho độ phổ biến chủ đề. TikTok video search cho mức độ nội dung (số lượt thích là chỉ số nội dung, không phải nhu cầu).
4. **Số liệu "đã bán"** trên Lazada/Alibaba/1688 là nhãn làm tròn của sàn → `verification=observed`, `confidence` ≤ 0.6.
