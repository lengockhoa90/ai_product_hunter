# PHASE 1 AUDIT — AI PRODUCT HUNTER

> Ngày kiểm toán: 2026-11-24 (10:40–11:10 giờ VN) · Người thực hiện: Claude · Yêu cầu: Khoa Le
> Phạm vi: `docs/SYSTEM_ARCHITECTURE.md` v0.1 và `docs/IMPLEMENTATION_ROADMAP.md` v0.1 (Phase 1), đối chiếu với môi trường Claude thực tế.
> Nguyên tắc: mọi kết luận đều kèm bằng chứng; điều gì chưa kiểm chứng được thì ghi UNVERIFIED. Kiểm toán này không sửa kiến trúc.

## Lưu ý trước khi đọc

Trước yêu cầu kiểm toán này, theo lời nhắn "Bắt đầu phase 2" lúc 10:26, Phase 2 đã được làm và nằm ở nhánh `phase-2-core`, [PR #1](https://github.com/lengockhoa90/ai_product_hunter/pull/1), **chưa merge**. `main` chỉ có tài liệu Phase 1. Kiểm toán này đánh giá Phase 1. Khi một kết luận dựa trên bằng chứng lấy từ lần kiểm thử ở Phase 2, báo cáo ghi rõ "(bằng chứng Phase 2)". Trong lúc kiểm toán không viết thêm mã Phase 2 nào.

---

## A. Tổng quan kiến trúc hiện tại

| Lớp | Thiết kế Phase 1 |
|---|---|
| Giao tiếp | Người dùng nhắn bằng ngôn ngữ tự nhiên trong project chat, bộ điều phối của dự án mở một thread Claude Code trên cloud cho mỗi nhiệm vụ |
| Điều phối | Skill `aph-orchestrator` phân loại yêu cầu, lên kế hoạch, gọi Agent theo thứ tự và kiểm tra cổng chất lượng |
| Agent | 7 Skill (`aph-*`) chạy tuần tự trong cùng một phiên, bàn giao cho nhau qua kho dữ liệu |
| Thu thập | Trình duyệt Claude trên PC người dùng (qua remote-devices) và WebSearch |
| Tính toán | Script Python `tools/aph.py`: chống trùng lặp, chấm điểm, Data Confidence, lợi nhuận |
| Lưu trữ | `/mnt/project-files/ai-product-hunter/data/`: ghi nối tiếp (append-only), mỗi lần chạy một file, cộng một chỉ mục SQLite có thể dựng lại |
| Lập lịch | Scheduled task của dự án (cloud). Phương án dự phòng: scheduled task của Cowork desktop |
| Báo cáo | Markdown trong `reports/` và một Artifact dashboard |

---

## B. Bảng đánh giá 8 hạng mục

| # | Hạng mục | Kết luận | Lý do chính (chi tiết ở dưới) |
|---|---|---|---|
| 1 | Architecture | **PARTIAL** | Phân lớp hợp lý, không có Agent trùng chức năng. Có 1 thành phần không triển khai được (dự phòng Cowork desktop), và lớp thu thập phụ thuộc vào một máy duy nhất |
| 2 | Claude Integration | **PARTIAL** | Gọi trình duyệt trên PC từ cloud đã được kiểm chứng. Có 2 giả định sai: Cowork desktop ghi được vào thư mục dự án, và việc chặn mạng là giới hạn cố định |
| 3 | Agent System | **UNVERIFIED** | Ở Phase 1 chưa có Agent nào. Hợp đồng bàn giao mới chỉ có trên giấy. Kiểm soát đầu ra chỉ được mô tả |
| 4 | Data Collection | **PARTIAL** | Google, Google Trends, TikTok, Lazada, Alibaba, 1688 đọc được. **Shopee không thu thập được** (CAPTCHA). Không có nguồn dữ liệu bán hàng của TikTok Shop. Mà đây chính là 2 kênh bán chính |
| 5 | Data Storage | **PARTIAL** | Thiết kế đáp ứng lịch sử, nguồn và thời điểm. Khóa file giữa nhiều thread chưa được chứng minh. Phát hiện 1 lỗi hiệu năng nghiêm trọng (bằng chứng Phase 2) |
| 6 | Scheduling | **UNVERIFIED** | Chưa tạo scheduled task nào. Chưa biết phiên chạy theo lịch có dùng được trình duyệt trên PC không. Xử lý lỗi mới chỉ là một câu mô tả |
| 7 | Research Quality | **PARTIAL** | Có phân loại dữ liệu quan sát/suy luận, độ tin cậy, quy tắc dẫn nguồn. Nhưng không có cơ chế chứng minh Claude đã thực sự đọc trang nguồn |
| 8 | Scalability | **PARTIAL** | Thêm Agent, ngành hàng, nguồn đều dễ. Lưu trữ hàng nghìn sản phẩm làm được sau khi sửa lỗi hiệu năng. Nhưng **làm mới dữ liệu cho hàng nghìn sản phẩm định kỳ thì không khả thi** với trình duyệt và ngân sách token |

Không hạng mục nào đạt PASS đầy đủ.

### B1. Architecture — PARTIAL
- **Hợp lý:** phân lớp rõ ràng (điều phối, Agent, thu thập, công cụ, lưu trữ). Bàn giao qua dữ liệu thay vì qua trí nhớ hội thoại nên có thể chạy lại từng bước.
- **Trùng lặp:** 7 Agent không chồng chéo. Có một điểm chồng nhẹ: Agent 03 (Demand) và Agent 04 (Competitor) cùng đọc đánh giá khách hàng. Phase 1 chưa quy định ai thu thập, ai phân tích, nên có thể mở cùng một trang hai lần.
- **Trùng với skill có sẵn:** `trend-hunter-dtm` đã có quy trình đọc Google Trends và TikTok CSI. Phase 1 đã nêu việc tách biệt, nhưng chưa nói rõ công cụ đọc (`cong_cu/doc_csi.js` trên PC) sẽ được dùng chung hay viết lại.
- **Không triển khai được:** phương án dự phòng "Cowork desktop scheduled task chạy cùng skill trên PC, kết quả ghi về cùng thư mục dữ liệu" (SYSTEM_ARCHITECTURE mục 2.2 và mục 6). Bằng chứng: `/mnt/project-files` là một ổ FUSE rclone trong container cloud (`mount` → `fuse.rclone … /mnt/attach/project-files`), còn `get_device_info` của PC báo `connectedFolders: []`. Một tác vụ chạy trên PC không có đường ghi trực tiếp vào thư mục này.
- **Điểm lỗi đơn lẻ:** mọi dữ liệu sàn TMĐT/MXH đều phải đi qua một trình duyệt trên một PC Windows (`admin-pc`).

### B2. Claude Integration — PARTIAL

| Khả năng | Trạng thái | Bằng chứng |
|---|---|---|
| Project chat → thread Claude Code | ✅ Hỗ trợ trực tiếp | Thread này được bộ điều phối mở |
| Gọi trình duyệt Claude trên PC từ cloud | ✅ Đã kiểm chứng | `Claude_Browser__navigate` / `get_page_text` chạy được nhiều lần trong thread này |
| Thư mục dùng chung giữa các thread | ✅ | `/mnt/project-files` đọc/ghi được |
| Skill tài khoản | ⚠️ Cần thao tác thủ công | Chỉ tạo được qua thẻ đề xuất để người dùng tự lưu (`propose_skills`). Bản trên đĩa chỉ là cache chỉ đọc |
| Scheduled task trên cloud | ⚠️ Có công cụ, chưa chạy thử | `list_triggers` → 0 task. Mỗi lần tạo task đều cần người dùng đồng ý |
| Scheduled task của Cowork desktop | ❌ Không điều khiển được từ cloud | `list_triggers` ghi rõ: "Scheduled tasks stored locally by the Cowork desktop app are not listed" |
| Phiên chạy theo lịch dùng trình duyệt PC | ❓ UNVERIFIED | Thiết bị được liên kết "với thread này". Chưa biết phiên do lịch tạo ra (nhất là phiên mới cho mỗi lần chạy) có được liên kết hay không |
| Claude Docs / Artifact cho báo cáo | ✅ Có công cụ | Chưa dùng |

**Giả định sai trong Phase 1:**
1. **"Container cloud bị chặn mạng" được ghi như một giới hạn cố định (L1).** Thực tế, `get_environment` cho thấy môi trường `Default` đang ở chế độ `network.type = limited` với `allowed_hosts: []`. Đây là **cấu hình**, người dùng có thể thêm host trong cài đặt môi trường. Phase 1 đã bỏ qua phương án cho cloud truy cập trực tiếp Google Trends, Alibaba, 1688 khi PC tắt. Phương án này chưa được kiểm chứng: các trang đó có thể chặn IP của máy chủ.
2. **Dự phòng Cowork desktop ghi được vào kho dữ liệu dự án:** sai (xem B1).
3. **Gọi gộp "Claude Cowork" là bộ điều phối:** Phase 1 dùng chữ "Cowork" cho cả project chat lẫn ứng dụng desktop. Thực tế đó là hai thứ khác nhau: project chat và thread chạy trên cloud, còn app desktop thì cung cấp trình duyệt, computer use và lịch riêng. Hai bên chỉ nối với nhau qua công cụ remote-devices, và chỉ khi PC bật.

### B3. Agent System — UNVERIFIED
- **Tính khả thi về cơ chế:** Agent = Skill là cách hợp lý trên Claude, vì Skill là cơ chế có sẵn và không cần tự xây runtime. Nhưng ở Phase 1 **chưa có Agent nào** để kiểm chứng việc nhận nhiệm vụ, thực hiện và bàn giao.
- **Bàn giao:** hợp đồng (input `product_id`/`run_id`, output là bản ghi trong kho) mới chỉ có trong tài liệu. Ở Phase 2 đã có công cụ ghi/đọc và các bài test logic (bằng chứng Phase 2: 24/24 unit test), nhưng **chưa có lần chạy nào trong đó một Agent bàn giao cho Agent kế tiếp**.
- **Kiểm soát đầu ra:** Phase 1 chỉ mô tả "cổng kiểm tra". Ở Phase 2, công cụ đã từ chối các trường hợp: thiếu URL khi ghi `observed`, `estimated` thiếu khoảng giá trị, sai giá trị enum, trường lạ (bằng chứng Phase 2: test `TestSourceRules`). Không có cơ chế kiểm tra chéo *nội dung* (giá ghi vào có đúng với giá trên trang không).
- **Rủi ro:** một phiên chạy cả 7 Agent ở độ sâu `deep` sẽ dùng rất nhiều ngữ cảnh và thời gian. Chưa đo được một lần deep-dive tốn bao nhiêu token hay bao nhiêu phút.

### B4. Data Collection — PARTIAL
Bằng chứng: kiểm thử thật ngày 2026-11-24 bằng trình duyệt Claude, ghi trong `docs/SOURCE_ACCESS_MATRIX.md` (bằng chứng Phase 2). Lần thử Shopee và 1688 đầu tiên được làm ở Phase 1.

| Nguồn | Truy cập | Đăng nhập | Thu thập tự động | Ghi chú |
|---|---|---|---|---|
| Shopee | ❌ | Đã đăng nhập vẫn bị | Không | `/verify/traffic` rồi `/verify/captcha` ngay ở trang tìm kiếm |
| TikTok tìm video | ✅ | Có (đã đăng nhập) | Có, số lượng nhỏ | `/search?q=` báo lỗi máy chủ, `/search/video?q=` thì chạy |
| TikTok CSI | ✅ | Có | Có | Độ phổ biến chủ đề, % tăng |
| TikTok Shop (dữ liệu bán hàng) | ❌ | — | Không | Chưa có nguồn công khai nào cho lượt bán hay GMV. Chưa thử trang web TikTok Shop |
| Google Search VN | ✅ | Không | Có, số lượng nhỏ | Chưa thử với khối lượng lớn, có thể bị "unusual traffic" |
| Google Trends | ✅ | Không | Có | Đọc được bảng số theo tuần. Trang báo **"phiên bản cũ sắp ngừng hoạt động"**, nên phải có kế hoạch chuyển sang trang mới |
| 1688 | ✅ | Không | Có, số lượng nhỏ | Từ khóa phải mã hóa GBK (Phase 1 thử UTF-8 thì ra 0 kết quả) |
| Alibaba.com | ✅ | Không | Có, số lượng nhỏ | Giá hiển thị bằng VND, có MOQ, số năm hoạt động của NCC |
| Lazada VN | ✅ | Không | Có, số lượng nhỏ | Có tổng số sản phẩm, giá, đã bán |
| Taobao, Tiki, Amazon | ❓ | — | UNVERIFIED | Chưa kiểm thử |

- **Phương án thay thế khi không lấy được dữ liệu:** Phase 1 có nêu (ghi `access_failed`, dùng nguồn gián tiếp). Nhưng **chưa có phương án thật cho Shopee**, trong khi Shopee là kênh bán chính. Các phương án còn lại đều cần con người: người dùng tự giải CAPTCHA, hoặc xuất dữ liệu từ Seller Center. Cả hai đều chưa được thử.
- **Điều khoản sử dụng:** chưa xem điều khoản của Lazada, Alibaba, 1688, TikTok về truy cập tự động (UNVERIFIED).

### B5. Data Storage — PARTIAL
| Yêu cầu | Thiết kế | Kiểm chứng |
|---|---|---|
| Theo dõi dài hạn | Append-only, giữ nguyên lịch sử | ✅ Hàm `show` trả cả lịch sử lẫn giá trị hiện tại (bằng chứng Phase 2) |
| Chống trùng lặp | function_key, alias, độ giống tên | ✅ Thêm lại bằng tên tiếng Anh/Trung thì nhận ra sản phẩm cũ (bằng chứng Phase 2, dữ liệu thật) |
| Lịch sử biến động | Mỗi observation có `observed_at` và `collected_at` | ✅ |
| Nguồn và thời điểm | Bắt buộc `source_url` với dữ liệu observed/reported | ✅ |
| Ghi đồng thời từ nhiều thread | Khóa bằng file `O_EXCL` | ⚠️ **Chỉ đúng trong một container**: 20 tiến trình cùng tranh thì đúng 1 thắng, 8 tiến trình ghi nối cùng lúc 1.600 dòng không hỏng dòng nào. Nhưng thư mục là rclone FUSE: **giữa hai container khác nhau, khóa không được bảo đảm** (UNVERIFIED) |
| Hiệu năng ở quy mô lớn | — | ❌ **Lỗi** (bằng chứng Phase 2, đo trên dữ liệu tổng hợp trong thư mục tạm, chỉ để đo tốc độ): với 2.000 sản phẩm và 100.000 bản ghi, `list` mất **549 giây**, vì mỗi sản phẩm lại quét toàn bộ bản ghi. `match` mất 0,12 giây, `reindex` 1,2 giây, `duplicates` ước tính khoảng 55 giây |
| Bố cục trong tài liệu | Phase 1 ghi `observations/`, `listings/` … | ⚠️ Mã Phase 2 dùng `records/` (đã cập nhật v0.2 trên nhánh, chưa merge) |

### B6. Scheduling — UNVERIFIED
- **Cơ chế:** scheduled task của dự án (`create_trigger`). Có 3 kiểu: đánh thức lại chính phiên này, đánh thức một phiên khác, hoặc tạo phiên mới mỗi lần chạy. Có thể gửi thông báo đẩy hoặc email khi xong.
- **Có thực sự chạy định kỳ không:** công cụ có sẵn, nhưng **chưa chạy lần nào** (0 task).
- **Phụ thuộc vào máy:** dữ liệu sàn TMĐT cần PC bật và app Claude desktop đang mở. Phiên chạy theo lịch có được liên kết với PC không: **UNVERIFIED**.
- **Xử lý thất bại:** Phase 1 chỉ có "ghi `device_offline` và báo trong Daily Report". Còn thiếu: phát hiện một lần chạy không hoàn tất (`list_triggers.last_run.status` có sẵn nhưng chưa dùng), chạy lại, cảnh báo khi trượt liên tiếp nhiều lần, và chống chạy chồng hai phiên cùng lúc.

### B7. Research Quality — PARTIAL
| Cơ chế | Phase 1 | Đánh giá |
|---|---|---|
| Xác minh nguồn | Quy tắc "phải mở trang gốc" | ⚠️ Công cụ chỉ kiểm tra được *có* URL, không kiểm tra được Claude **đã đọc** trang đó và con số có khớp hay không |
| Phân biệt thực tế và suy luận | 5 mức `observed/reported/user_input/estimated/inferred` | ✅ Công cụ ép buộc được (bằng chứng Phase 2) |
| Độ tin cậy | Data Confidence độc lập với điểm, có trần 74 | ✅ Logic đã được test (bằng chứng Phase 2), nhưng **các hệ số (0,3–1,0, chu kỳ bán rã) là tự đặt, chưa được hiệu chỉnh** với kết quả thực tế |
| Tránh AI tự tạo số liệu | Quy tắc, cộng với việc tính toán bằng script | ⚠️ Giảm được rủi ro tính sai, nhưng **không ngăn được việc ghi một con số không có trên trang**. Phase 1 đã thấy một rủi ro cụ thể: phần "Thông tin tổng quan do AI tạo" của Google đưa ra tên nhà phân phối. Quy tắc đã cấm dùng phần này làm nguồn, nhưng đó chỉ là quy tắc |

### B8. Scalability — PARTIAL
- **Thêm Agent:** dễ, chỉ cần thêm skill và giá trị enum `agent`.
- **Thêm ngành hàng:** dễ, qua `config/categories.json`.
- **Thêm nguồn dữ liệu:** dễ về dữ liệu (thêm `source_type` hoặc `platform`). Mỗi nguồn mới vẫn phải được kiểm thử truy cập.
- **Hàng nghìn sản phẩm:**
  - Lưu trữ: được, sau khi sửa lỗi O(P×R) ở `list`/`status` (xem B5).
  - **Làm mới dữ liệu thì không khả thi.** Mỗi lần đọc một trang qua trình duyệt mất khoảng 5–15 giây và tốn token cho nội dung trang. Ước tính sơ bộ (chưa đo): làm mới 1.000 sản phẩm × 3 nguồn ≈ 3.000 lượt tải trang ≈ 4–12 giờ trình duyệt mỗi lượt, chưa tính giới hạn sử dụng của gói Claude và nguy cơ bị sàn chặn.
  - **Kết luận:** có thể *lưu* hàng nghìn sản phẩm, nhưng chỉ *theo dõi tích cực* được vài chục sản phẩm mỗi ngày.

---

## C. Lỗi kiến trúc cần khắc phục

| # | Lỗi | Mức độ | Khắc phục |
|---|---|---|---|
| C1 | Dự phòng Cowork desktop không ghi được vào kho dữ liệu dự án | Cao | Bỏ phương án này, hoặc thiết kế bàn giao qua một thư mục trên PC (ví dụ `My Drive/AI_PRODUCT_HUNTER/inbox`) mà thread cloud đọc qua `device_stage_files` |
| C2 | Không có phương án thu thập dữ liệu cho Shopee và TikTok Shop, 2 kênh bán chính | Cao | Định nghĩa chế độ thu thập có người hỗ trợ (người dùng giải CAPTCHA hoặc xuất dữ liệu Seller Center), dùng Lazada/Google/TikTok làm tín hiệu thay thế, và hạ trọng số "competition" khi thiếu Shopee |
| C3 | Chặn mạng ở cloud bị coi là giới hạn cố định | Trung bình | Thử thêm `trends.google.com`, `alibaba.com`, `1688.com` vào danh sách host cho phép (cần người dùng duyệt), để lịch chạy không phụ thuộc vào PC |
| C4 | Khóa file giữa nhiều thread trên rclone FUSE không được bảo đảm | Trung bình | Quy tắc "một người ghi": chỉ thread hoặc lịch chạy đang giữ một run mới được sửa `products/`; bản ghi luôn nằm ở file riêng của mỗi run; `duplicates` chạy sau mỗi lần chạy |
| C5 | Lập lịch chưa có cơ chế xử lý thất bại | Trung bình | Ghi heartbeat cho mỗi run, kiểm tra `last_run.status` ở lần chạy sau, chạy lại tối đa 1 lần, báo người dùng khi trượt 2 lần liên tiếp |
| C6 | Không có bằng chứng Claude đã đọc trang nguồn | Trung bình | Bắt buộc mỗi observation `observed` lưu `evidence_excerpt`, tức đoạn văn bản chứa con số trích nguyên văn từ trang, để công cụ kiểm tra con số có nằm trong đoạn đó |
| C7 | Lỗi hiệu năng `list`/`status` (O(sản phẩm × bản ghi)) | Trung bình | Đọc bản ghi một lần, nhóm theo sản phẩm, hoặc đọc từ `index.sqlite` |
| C8 | Phân công Demand/Competitor chồng nhau ở khâu đọc đánh giá | Thấp | Competitor thu thập listing và đánh giá; Demand chỉ phân tích những gì đã có trong kho |
| C9 | Phụ thuộc vào trang Google Trends phiên bản cũ sắp ngừng | Thấp | Kiểm thử trang Khám phá mới trước Phase 3 |

---

## D. Giả định kỹ thuật chưa được chứng minh

1. Phiên do scheduled task tạo ra dùng được trình duyệt Claude trên PC.
2. Account skills (ví dụ `aph-orchestrator`) được nạp trong phiên chạy theo lịch.
3. Một lần deep-dive gồm 7 Agent vừa trong ngữ cảnh và giới hạn sử dụng của một phiên.
4. Các sàn (Google, Lazada, 1688, Alibaba, TikTok) không chặn khi truy cập đều đặn mỗi ngày.
5. Cloud truy cập trực tiếp được Google Trends hoặc 1688 sau khi thêm host cho phép.
6. Khóa file hoạt động đúng giữa hai container khác nhau.
7. Hệ số Data Confidence và rubric chấm điểm phản ánh đúng chất lượng cơ hội.
8. TikTok Shop có trang web công khai đọc được giá hoặc lượt bán.
9. Người dùng sẵn sàng giải CAPTCHA Shopee định kỳ.
10. Truy cập tự động ở mức nhỏ không vi phạm điều khoản của từng nền tảng.

---

## E. Giới hạn thực tế của Claude (đã kiểm chứng)

1. Không tự vượt CAPTCHA, không nhập mật khẩu, không đặt hàng hay thanh toán. Đây là giới hạn an toàn, không thể thay đổi.
2. Phiên cloud là tạm thời; chỉ `/mnt/project-files`, repo Git và project memory là còn lại lâu dài.
3. Skill chỉ được lưu khi người dùng bấm duyệt.
4. Scheduled task cần người dùng đồng ý, tần suất tối thiểu thường là 1 giờ. Task của Cowork desktop không điều khiển được từ cloud.
5. Trình duyệt chỉ chạy được khi PC bật và Claude desktop đang mở. Đọc trang tốn token theo độ dài nội dung.
6. WebSearch chỉ mục thiên về quốc tế (bằng chứng: truy vấn tiếng Việt về cảm biến rò rỉ nước trả về chủ yếu trang nước ngoài).
7. Mạng cloud theo cấu hình môi trường: hiện là `limited`, không có host bổ sung.
8. GitHub: GraphQL không dùng được, phải dùng REST.

---

## F. Đề xuất kiến trúc điều chỉnh

Đây là đề xuất, chưa áp dụng.

1. **Hai chế độ thu thập:**
   - *Chế độ A (PC bật):* dùng trình duyệt Claude cho TikTok, Lazada, Google, 1688, Alibaba.
   - *Chế độ B (PC tắt):* cloud truy cập trực tiếp những host được cho phép (nếu kiểm chứng được), cộng WebSearch.
   - Mỗi run ghi rõ mình chạy ở chế độ nào.
2. **Kênh dữ liệu có người hỗ trợ cho Shopee và TikTok Shop:** một thư mục "inbox" trên PC để người dùng thả file xuất từ Seller Center hoặc ảnh chụp. Thread đọc qua `device_stage_files` và ghi với `verification=user_input`.
3. **Bỏ phương án dự phòng Cowork desktop** ghi trực tiếp vào kho. Nếu cần chạy trên PC, chỉ cho phép ghi vào inbox.
4. **Phân tầng theo dõi:**
   - *Tier 1:* tối đa 20 sản phẩm, làm mới hằng ngày.
   - *Tier 2:* tối đa 200 sản phẩm, làm mới hằng tuần.
   - *Tier 3:* phần còn lại, chỉ lưu trữ và làm mới khi có tín hiệu.
5. **Bằng chứng trích dẫn** (`evidence_excerpt`) bắt buộc cho dữ liệu `observed`.
6. **Vận hành lịch có giám sát:** heartbeat cho mỗi run, chống chạy chồng, chạy lại 1 lần, cảnh báo khi trượt 2 lần.
7. **Một người ghi** cho hồ sơ sản phẩm, kiểm tra trùng lặp sau mỗi run.

---

## G. Việc cần hoàn thành trước Phase 2 (trước khi merge PR #1)

| # | Việc | Cần người dùng? |
|---|---|---|
| G1 | Tạo **một** scheduled task chạy một lần để kiểm chứng: phiên theo lịch có dùng được trình duyệt PC, có nạp skill, có ghi được vào kho không. Xoá ngay sau khi thử | Có, cần đồng ý tạo task |
| G2 | Thử thêm `trends.google.com`, `www.alibaba.com`, `s.1688.com` vào host cho phép của môi trường cloud, rồi kiểm thử truy cập trực tiếp | Có, cần duyệt thay đổi môi trường |
| G3 | Chọn phương án dữ liệu Shopee/TikTok Shop: tự giải CAPTCHA, xuất Seller Center, hay bỏ Shopee khỏi các chỉ số tự động | Có |
| G4 | Sửa tài liệu kiến trúc theo C1, C3, C4, C8 (thành v0.3) | Không |
| G5 | Bổ sung vào thiết kế: `evidence_excerpt`, phân tầng theo dõi, xử lý lỗi lịch | Không |
| G6 | Sửa lỗi hiệu năng C7 trong PR #1 và thêm test hiệu năng | Không |
| G7 | Đo chi phí một lần deep-dive thật (thời gian, số trang) cho 1 sản phẩm | Không |
| G8 | Kiểm thử Taobao, Tiki, trang web TikTok Shop và trang Google Trends mới | Không |

---

## H. Kết luận

**Có nên chuyển sang Phase 2 chưa? Chưa nên coi Phase 2 là xong và chưa nên merge PR #1.**

- Kiến trúc Phase 1 **đúng hướng**: Skill cho Agent, script cho tính toán, kho ghi nối tiếp, dẫn nguồn và Data Confidence. Không có thành phần nào giả định dùng Claude API hay n8n.
- Nhưng còn **2 vấn đề có thể làm hỏng mục tiêu**:
  1. Không thu thập được dữ liệu Shopee, và chưa có dữ liệu bán hàng TikTok Shop, trong khi đây là 2 kênh bán chính (C2).
  2. Tính "tự động theo lịch" chưa được chứng minh. Toàn bộ thu thập phụ thuộc vào một PC, và chưa biết phiên chạy theo lịch có dùng được PC đó không (G1).
- **Cần khắc phục trước:** G1, G2, G3 (cần người dùng quyết hoặc duyệt), sau đó G4–G6. Khi G1–G3 có kết quả thật, Phase 2 (PR #1) có thể được chỉnh theo kiến trúc v0.3 rồi mới merge.

---

## I. Kết quả kiểm chứng G1–G3 (2026-11-24, 10:55–11:10 giờ VN)

Khoa Le đã đồng ý cho chạy G1, G2 và chọn phương án cho G3 lúc 10:54.

### G1 — Chạy thử một scheduled task một lần (phiên mới mỗi lần chạy)
Bằng chứng: `/mnt/project-files/ai-product-hunter/audit/scheduled_test_result.json` (chạy lúc 04:06:33Z, `last_run.status = SUCCEEDED`). Task đã bị xoá sau khi thử.

| Kiểm tra | Kết quả |
|---|---|
| Trình duyệt Claude trên PC | ❌ **Không có**: phiên chạy theo lịch không có công cụ `Claude_Browser`, không có công cụ thiết bị nào |
| Thư mục trên PC | ❌ Không có |
| Skill `aph-orchestrator` | ❌ Không có. Skill nằm trên nhánh PR #1 chưa merge, và người dùng cũng chưa lưu thẻ skill |
| Repo | ✅ Có (nhánh `main`) |
| WebSearch | ✅ 15 kết quả |
| Ghi vào thư mục dự án | ✅ |
| Mạng trực tiếp (chỉ thử URL gốc) | Kết nối được (301/200) |

**Kết luận G1:** phiên chạy theo lịch trên cloud **không dùng được trình duyệt trên PC**. Lịch cloud chỉ làm được: WebSearch, xử lý và chấm điểm lại dữ liệu đã có, tạo báo cáo.

### G2 — Cho cloud truy cập trực tiếp (đã thêm `trends.google.com`, `www.alibaba.com`, `s.1688.com`)
Kiểm thử bằng curl từ thread này với các trang có dữ liệu thật:

| Trang | Kết quả |
|---|---|
| Google Trends explore | ❌ HTTP **429** "Too Many Requests" (trang robot của Google) |
| Alibaba tìm kiếm | ❌ HTTP 200 nhưng là trang **captcha/punish** |
| 1688 tìm kiếm | ❌ Chuyển hướng tới `/_____tmd_____/punish` (chống bot) |

**Kết luận G2:** truy cập trực tiếp từ cloud **không thu thập được dữ liệu**. Ở lần chạy lịch, URL gốc trả 200/301, nhưng các trang dữ liệu đều bị chặn. Phương án "Chế độ B" ở mục F1 **bị loại**. Ba host vẫn đang nằm trong danh sách cho phép, không gây hại; có thể gỡ nếu muốn.

### G3 — Shopee
Khoa sẽ tự giải CAPTCHA Shopee trong trình duyệt Claude. Việc này chỉ làm được trong một thread có người đang dùng và PC đang được liên kết, không làm được trong lịch chạy.

### Đánh giá lại
| Hạng mục | Trước | Sau kiểm chứng |
|---|---|---|
| 6. Scheduling | UNVERIFIED | **FAIL** cho thu thập dữ liệu sàn theo lịch trên cloud · PASS cho các việc chỉ cần WebSearch, kho dữ liệu và báo cáo |
| 4. Data Collection | PARTIAL | PARTIAL: mọi dữ liệu sàn TMĐT/MXH chỉ lấy được qua trình duyệt trên PC, trong phiên có liên kết thiết bị |

### Hệ quả kiến trúc
Không có cách nào trong môi trường cloud hiện tại để **tự động hoàn toàn** việc thu thập dữ liệu sàn theo lịch. Có 3 hướng:

1. **Lịch chạy trên Cowork desktop (PC)**
   - Lịch đặt trên app Claude desktop, chạy ngay trên PC. Ở đó có trình duyệt và có thư mục máy.
   - Kho dữ liệu chuyển sang một thư mục trên PC (ví dụ trong `My Drive`). Thread cloud đọc kho này qua công cụ thư mục khi PC được liên kết.
   - Việc người dùng phải tự làm: tạo task trong app desktop.
   - Chưa kiểm chứng: task desktop có dùng được trình duyệt Claude không.
2. **Bán tự động**
   - Lịch cloud hằng ngày chỉ làm WebSearch, tính lại điểm, ra báo cáo và danh sách việc cần thu thập.
   - Phần thu thập qua trình duyệt chạy khi người dùng mở một thread (ví dụ "chạy thu thập hôm nay"), khoảng 10–20 phút mỗi ngày.
3. **Chỉ cloud**
   - Chỉ dùng WebSearch, nên dữ liệu thị trường Việt Nam rất yếu (xem E6).
