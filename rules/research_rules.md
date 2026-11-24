# Quy tắc nghiên cứu (bắt buộc cho mọi Agent)

## 1. Bằng chứng
- Mọi con số đưa vào kho phải đi qua `aph.py add --kind observation` (hoặc listing/supplier) với `source_url` thật mà Claude **đã mở và đọc** trong lần chạy này.
- Không lấy số từ trí nhớ, từ phần "Thông tin tổng quan do AI tạo" của Google, hay từ đoạn trích kết quả tìm kiếm mà chưa mở trang gốc. Đoạn trích chỉ dùng để quyết định có nên mở trang hay không.
- `verification`:
  - `observed`: thấy trực tiếp trên trang nguồn (giá, MOQ, số người bán đếm được, bảng Google Trends).
  - `reported`: một nguồn khác nói lại (bài báo, blog tổng hợp phí).
  - `user_input`: người dùng cung cấp.
  - `estimated`: ước tính có khoảng `range_low`/`range_high` và `note` giải thích cách ước tính.
  - `inferred`: suy luận của Claude, bắt buộc `note`. Không bao giờ dùng `inferred` cho giá hay lượt bán.
- `confidence` (0–1): 0.9 cho số đọc rõ trên trang chính thức; 0.6 cho nhãn làm tròn ("đã bán 1,2k"); 0.4 trở xuống cho số gián tiếp.
- `observed_at`: ngày dữ liệu thuộc về (ví dụ tuần trong Google Trends), khác với `collected_at` (công cụ tự ghi).

## 2. Truy cập nguồn
- Thu thập qua trình duyệt Claude trên PC người dùng (`mcp__remote-devices__Claude_Browser__*`) hoặc WebSearch. Xem `docs/SOURCE_ACCESS_MATRIX.md`.
- Gặp CAPTCHA, trang xác minh, yêu cầu đăng nhập, lỗi: **dừng với nguồn đó**, ghi `aph.py log-access --status captcha|anti_bot|login_required|timeout|empty|error`, chuyển nguồn khác. Không thử vượt qua.
- Chỉ người dùng mới được tự giải CAPTCHA trong khung trình duyệt; Claude chỉ đọc trang sau khi họ làm xong và nói cho Claude biết.
- Giới hạn số trang mỗi lần chạy theo `config/depth_budgets.json`. Không mở hàng loạt trang sản phẩm liên tục trên cùng một sàn.
- Không đặt hàng, thanh toán, nhắn tin nhà cung cấp, thêm vào giỏ, theo dõi shop, hay thay đổi gì trên tài khoản của người dùng.
- 1688: từ khóa tiếng Trung phải mã hóa GBK (`python3 -c "import urllib.parse;print(urllib.parse.quote('漏水传感器'.encode('gbk')))"`).

## 3. Kết luận
- **Xu hướng**: cần ≥ 2 mốc thời gian và ≥ 2 nguồn độc lập. Một video hay một bài đăng không phải xu hướng.
- **Nhu cầu**: lượt xem/lượt thích là tín hiệu nội dung (`metric=content_signal`), không phải nhu cầu. Nhu cầu cần ít nhất một trong: mức tìm kiếm, số đã bán, câu hỏi/phàn nàn lặp lại của người mua.
- **"Sản phẩm mới"**: phải có bằng chứng sản phẩm mới trên thị trường (ngày ra mắt, ngày đăng bán đầu tiên, số người bán VN thấp), ghi ở `new_to_market_evidence`. Việc người nghiên cứu mới biết đến không phải bằng chứng.
- **Lợi nhuận**: chỉ kết luận khi `aph.py profit` trả `status=complete`. Thiếu giá nhập → ghi "cần liên hệ báo giá" (`quote_status=need_quote`).
- **Giá bán tham khảo** của đối thủ là `price` quan sát, không phải giá bán của mình.
- **Nhà cung cấp**: độ tin cậy dựa vào tín hiệu kiểm chứng được (số năm hoạt động hiển thị trên sàn, chứng nhận có số hiệu, địa chỉ/MST tra được, đánh giá giao dịch). Lời quảng cáo không phải tín hiệu.
- Dữ liệu cũ hơn hạn trong `config/confidence.json` phải gắn nhãn "dữ liệu cũ" khi báo cáo.

## 4. Báo cáo
- Mỗi nhận định quan trọng kèm `[obs_xxx]` → URL + ngày.
- Luôn có mục "Không truy cập được" liệt kê các lần `log-access` không phải `ok`.
- Phân biệt rõ "Dữ liệu quan sát" và "Nhận định của AI".
