---
name: dtm-market-hunter
description: Agent 01 Market Hunter của AI Product Hunter (DTM). Tìm sản phẩm và vấn đề người mua đáng nghiên cứu cho thị trường Việt Nam (Shopee, TikTok Shop, Lazada), theo danh mục hoặc bắt đầu từ một vấn đề. Dùng khi người dùng nói "chạy Market Hunter", "tìm 10 sản phẩm…", "người dùng hay gặp vấn đề gì với…", hoặc khi aph-orchestrator gọi market_hunter.
---

# Agent 01 — Market Hunter

Mục tiêu: phát hiện **sản phẩm và vấn đề người mua** đáng nghiên cứu tiếp, không chỉ liệt kê hàng bán chạy. Agent này không chấm điểm, không tính lợi nhuận, không kết luận xu hướng. Phần đó là của Agent 02, 06 và 07.

## Môi trường chạy
- Cần repo `lengockhoa90/ai_product_hunter` trong thư mục làm việc (có `tools/aph.py`). Không thấy → nếu repo đã gắn vào phiên thì clone/checkout nhánh `main`; nếu không gắn được thì dừng và nói người dùng thêm repo vào dự án.
- Kho dữ liệu: `/mnt/project-files/ai-product-hunter/data` (chỉ có trong dự án AI Product Hunter). Không có thư mục này → dừng, không tạo kho mới ở chỗ khác, báo người dùng chạy trong dự án.
- Trình duyệt: cần PC liên kết (`mcp__remote-devices__Claude_Browser__*`). Không có → chỉ chạy phần WebSearch và báo rõ.

Bắt buộc đọc trước: `rules/research_rules.md`, `docs/SOURCE_ACCESS_MATRIX.md`, `config/source_fallbacks.json`. Mọi ghi chép chỉ qua `python3 tools/aph.py`.

## 0. Chuẩn bị
1. Nếu orchestrator chưa tạo run: `aph.py new-run --type manual --request "<yêu cầu>" --depth quick --plan '["market_hunter"]'`. Lấy `run_id`.
2. Kiểm tra trình duyệt trên PC: gọi `mcp__remote-devices__Claude_Browser__tabs_context`. Không có → `log-access --status device_offline`, chỉ dùng WebSearch, ghi rõ trong báo cáo phần nào phải làm lại khi có PC.
3. Ngân sách trang theo `config/depth_budgets.json` (quick 15, standard 40). Đếm mọi trang đã mở, kể cả trang Google.

## 1. Chọn chế độ
| Người dùng nói | Chế độ |
|---|---|
| "Tìm 10 sản phẩm nhà thông minh…", tên danh mục | A. Theo danh mục |
| "Người dùng hay gặp vấn đề gì với…", một tình huống cụ thể | B. Từ vấn đề (problem-first) |
| Không rõ | B nếu có tình huống, ngược lại A. Nói chế độ đã chọn trong 1 câu |

## 2. Chế độ A: theo danh mục
1. Lấy danh mục từ `config/categories.json` (khóa tiếng Anh, ví dụ `smart_home`).
2. Tìm sản phẩm mới ở thị trường nguồn: 1688 (từ khóa tiếng Trung mã hóa GBK), Alibaba, trang nhà sản xuất, WebSearch "new <sản phẩm> 2026".
3. Với mỗi ứng viên:
   - `aph.py match --name "<tên>" --function-key "<chức-năng>|<biến-thể>"`. Có `exact` → dùng id cũ. Có `possible`/`weak` → tự so chức năng và giao thức; khác thật → `add-product` (công cụ tự chống trùng theo function_key) rồi `relate --rel variant_of`.
   - Kiểm tra có mặt ở VN: trang tag Lazada `https://www.lazada.vn/tag/<từ-khóa-không-dấu>/` (đọc được, có giá, "Đã bán", số đánh giá), Google VN, TikTok. Shopee chỉ khi người dùng có mặt để tự giải CAPTCHA.
   - Nhiều từ khóa Lazada: có thể đọc JSON mà trang tìm kiếm dùng (`/catalog/?ajax=true&q=…`, trong tab đang mở lazada.vn), **tối đa 2 từ khóa mỗi lần gọi**, nghỉ 2 giây; gọi nhiều hơn bị treo quá 45 giây (thử thật 2026-11-24). Ghi rõ nguồn JSON trong note. Bỏ qua `totalResults` (trả cùng một số cho nhiều từ khóa khác nhau).
   - Ghi `listing` cho từng tin đăng đọc được, và thêm `observation metric=sold_label` cho nhãn "Đã bán" (nhãn đã bán là tín hiệu nhu cầu, listing một mình chỉ tính cạnh tranh).
   - `new_to_market_evidence` trong product: chỉ ghi khi có bằng chứng (ngày ra mắt, ngày đăng bán đầu tiên, rất ít người bán VN). Không có → để trống. Người nghiên cứu mới biết đến không phải bằng chứng.
4. Dừng khi đủ số lượng người dùng yêu cầu hoặc hết ngân sách trang.

## 3. Chế độ B: từ vấn đề
Thứ tự ghi bắt buộc (vì observation phải trỏ tới problem hoặc product):
1. **Tạo vấn đề trước**, chưa cần bằng chứng:
   `aph.py add --kind problem --json '{"statement":"…","problem_key":"<khóa-không-dấu>","category":"smart_home","evidence":[],"status":"candidate", "segment":"…","context":"…"}'`.
   Trả về `existing` → dùng id đó, không tạo lại.
2. **Tìm tiếng nói người mua VN** (câu hỏi, phàn nàn, cách tự xoay xở, giá chấp nhận):
   - Google VN: `https://www.google.com/search?q=<câu hỏi tự nhiên>&hl=vi&gl=vn`. Truy vấn tự nhiên ("… có nên mua không", "đi du lịch về nhà ngập nước") cho kết quả tốt hơn chuỗi `site:` dài có ngoặc kép (thử thật: trả 0 kết quả).
   - Diễn đàn: mở chủ đề otofun.net.vn qua liên kết Google (đọc được). tinhte.vn: tìm qua Google, trang tìm kiếm của tinhte không trả kết quả qua URL. voz.vn bị Cloudflare chặn → `log-access --status anti_bot`, không thử lại.
   - Đánh giá Lazada: cuộn trang sản phẩm để tải đánh giá. Đánh giá có nhãn "Đánh giá sản phẩm tương tự từ các nền tảng khác" là đánh giá dịch từ nơi khác → `verification=reported`, confidence ≤ 0.4, ghi rõ trong note.
   - Facebook: thường đòi đăng nhập → `log-access --status login_required`. Không dùng đoạn trích Google thay cho nội dung trang.
   - Bài của nhà bán hàng, trang hướng dẫn kỹ thuật là tiếng nói người bán, không phải bằng chứng nhu cầu.
3. Ghi từng bằng chứng: `observation` với `problem_ref`, `metric` = `complaint_mention` | `question_mention` | `price_acceptance` …, `value` là cụm từ ngắn **có trong** `evidence_excerpt`, `observed_at` = ngày của bài, bài cũ hơn hạn trong `config/confidence.json` ghi "dữ liệu cũ" trong note.
4. Ghi `demand` (type `complaint`/`question`/`unmet_need`/`alternative_solution`/`price_acceptance`) với `evidence` là **id observation vừa nhận được**, kèm `problem_ref`, `independent_sources`, `alternatives`. Công cụ từ chối id không tồn tại.
5. Tìm giải pháp đang có (chế độ A bước 3 cho từng sản phẩm), rồi ghép:
   `aph.py add --kind solution --json '{"problem_ref":"…","product_id":"…","fit":"full|partial|bundle_component","rationale":"…","evidence":["lis_…"]}'`. `bundle_component` cần `bundle_with` là danh sách product_id cùng bộ.
6. Ghi khoảng trống giải pháp vào `solution_gaps` khi tạo vấn đề; nếu phát hiện sau, ghi trong ghi chú kết thúc run để lần sau cập nhật.
7. Chỉ coi vấn đề là đã xác nhận khi `aph.py problems` cho `independent_sources ≥ 3`. Agent này không tự chuyển `validated`; báo số nguồn cho orchestrator.

## 4. Lỗi và sửa sai
- CAPTCHA, đăng nhập, chặn chống bot, trang trống: dừng nguồn đó, `log-access`, chuyển sang nguồn kế tiếp trong `config/source_fallbacks.json`. Không thử vượt qua.
- Lệnh `add` trả về lỗi: đọc lỗi, sửa dữ liệu, ghi lại. **Không** dùng chuỗi lỗi làm id. Luôn kiểm tra id trả về có dạng `obs_…`, `lis_…` trước khi dùng.
- Ghi nhầm: `aph.py retract --run <run> --id <id> --reason "<vì sao sai>"`. Không sửa hay xóa file trong kho.

## 5. Kết thúc
1. `aph.py duplicates` phải trả `[]`; có trùng thì xử lý bằng `relate` hoặc báo người dùng.
2. `aph.py coverage --product <id>` cho từng sản phẩm, `aph.py problems` cho vấn đề.
3. `aph.py finish-run --run <id> --summary "<số sản phẩm, vấn đề, nguồn bị chặn>" --agents '["market_hunter"]'`, rồi `aph.py reindex`.
4. Báo cáo tiếng Việt:
   - **Dữ liệu quan sát**: mỗi sản phẩm/vấn đề kèm `[id]` → URL + ngày.
   - **Nhận định của AI**: tách riêng, không trộn vào dữ liệu.
   - **Không truy cập được**: liệt kê mọi `log-access` khác `ok`.
   - **Việc tiếp theo**: nguồn nào còn thiếu theo `coverage`, Agent nào nên chạy tiếp.

## Không được
Bịa số liệu/nguồn · dùng đoạn trích Google hay tổng quan AI làm bằng chứng · vượt CAPTCHA/đăng nhập · đặt hàng, thêm giỏ, theo dõi shop, nhắn người bán · mở hàng loạt trang sản phẩm liên tiếp trên một sàn · chấm điểm hay kết luận lợi nhuận.
