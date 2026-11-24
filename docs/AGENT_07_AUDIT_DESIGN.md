# Agent 07 Opportunity Evaluator: báo cáo Audit và Design

Ngày 24/11/2026 · Giai đoạn A (Audit) và B (Design) · **anh đã duyệt 15:27**; bản cập nhật thêm mục 7 (4 yêu cầu bổ sung A–D). Mục 7 thắng nếu khác các mục trước.

## Tóm tắt

- Kiến trúc hiện tại đã có gần đủ "khung" cho Agent 07: kho append-only, bản ghi `evaluation`, hàm `score()`, `gate()`, `compute_confidence()`, `trend_assess()`, `vendor_assessment()`, `max_unit_cost()`. Agent 07 **dùng lại** các hàm này, không dựng kho mới.
- Dữ liệu hiện **rất mỏng ở nhóm Nhu cầu**: chưa có Agent 03, 04; mỗi sản phẩm chỉ có 1 lần chụp "đã bán" trên Lazada. Nếu chạy hôm nay, gần như mọi sản phẩm sẽ ra **NEED MORE DATA**. Đây là kết quả đúng, không phải lỗi.
- Chỉ 3 sản phẩm (cảm biến cửa, công tắc, khóa vân tay) có đủ giá thị trường theo model, nhà cung cấp và trần giá nhập. 9 sản phẩm còn lại thiếu gần hết.
- Có 9 điểm lệch giữa tài liệu và code cần anh chốt trước khi sửa thành phần dùng chung (mục 3).

## 1. Kiến trúc hiện tại cho phép xây Agent 07 thế nào

| Thành phần có sẵn | Agent 07 dùng để làm gì |
|---|---|
| Kho JSONL append-only + SQLite (`reindex`) | Ghi bản ghi `evaluation` mới mỗi lần chạy; không sửa bản cũ. Lịch sử và so sánh giữa các lần chạy có sẵn. |
| `retraction` | Rút lại một đánh giá sai mà không xóa. |
| `compute_confidence()` + `config/confidence.json` | Độ tin cậy theo nguồn, loại xác minh, độ cũ, số nguồn độc lập. Dùng làm Data Confidence. |
| `trend_assess()` | Nhóm Xu hướng: giai đoạn chỉ kết luận khi ≥ 2 nguồn độc lập đồng ý. |
| `vendor_assessment()` (Agent 05) | Nhóm Nhà cung cấp: điểm 0–12, mức high/medium/low, `ask_vendor`. |
| `price_stats()`, `max_unit_cost()`, `discount_needed()` (Agent 06, PR #7) | Nhóm Tài chính: giá thị trường theo model, trần giá nhập, % chiết khấu cần xin. |
| `coverage()` | Kiểm tra `cost_price_known`, số nguồn mỗi tiêu chí. |
| `score()` + `gate()` + `config/scoring_weights.json` (v1) | Khung chấm có version, trần 74 khi tiêu chí quan trọng thiếu tin cậy. Agent 07 cần **phiên bản v2** (mục 5). |
| `research_status` (qualified_opportunity / research_more / watchlist / rejected) | Ánh xạ từ 4 trạng thái GO / CONDITIONAL GO / NEED MORE DATA / NO GO. |

Kết luận: không cần kho song song, không cần đổi kiến trúc tổng thể. Cần: một lệnh `evaluate`, một lệnh `eval-diff`, thêm kind `evaluation` vào schema, một bộ trọng số v2.

## 2. Mức hoàn chỉnh dữ liệu từ Agent 01–06

Kho có 12 sản phẩm.

| Agent | Trạng thái | Dữ liệu thực có |
|---|---|---|
| 01 Market Hunter | Có skill, đã chạy | 12 sản phẩm, vài bản ghi "đã bán" Lazada (1 lần chụp mỗi sản phẩm), 1 vấn đề người mua |
| 02 Trend Hunter | Có skill, đã chạy | Chỉ **cảm biến cửa** kết luận được `growing` (Google Trends + TikTok CSI). Còn lại `unknown` |
| 03 (nhu cầu / vấn đề) | **Chưa có skill** | 1 bản ghi demand (1 phàn nàn, 1 nguồn, `candidate`) cho vấn đề rò nước |
| 04 (cạnh tranh) | **Chưa có skill** | Không có bản ghi cạnh tranh riêng; chỉ suy ra được từ số tin đăng và nhãn "đã bán" trong listing của Agent 06 |
| 05 Supplier Hunter | Đã merge | 5 vendor_review; Hunonic, Rạng Đông 7/12 (trung bình), 3 shop Tuya thấp. Nhà cung cấp gắn cho 3 sản phẩm; cảm biến rò nước có 2 NCC nhưng chưa đánh giá |
| 06 Profit Analyst | PR #7 chưa merge | 49 tin Lazada, 7 model, 7 bản ghi `max_cost` dạng **trần** (chưa trừ chi phí shop). Chưa có Shopee, TikTok Shop |

Theo sản phẩm (chỉ 3 sản phẩm có dữ liệu ở nhiều nhóm):

| Sản phẩm | Nhu cầu | Xu hướng | Cạnh tranh | NCC | Tài chính |
|---|---|---|---|---|---|
| Cảm biến cửa `prd_a99ba65ad4` | mỏng (1 lần chụp) | growing | có listing theo model | 3 NCC, 2 trung bình | trần giá nhập 3 model |
| Công tắc `prd_282236522b` | mỏng | unknown | có listing | 3 NCC | trần 3 model |
| Khóa vân tay `prd_6cbde75147` | mỏng | unknown | listing trải rộng nhiều mẫu | 3 NCC; giá sỉ duy nhất là TTLock, **không phải** Tuya | trung vị chỉ tham khảo |
| 9 sản phẩm khác | mỏng hoặc không có | unknown / không có | không có | không có (trừ rò nước) | không có |

## 3. Dữ liệu còn thiếu và điểm lệch tài liệu / code

### Thiếu dữ liệu
1. **Giá nhập thật** (`dealer_quote`) cho mọi sản phẩm. Không có thì Gate Tài chính không thể kết luận lãi thật.
2. **Chi phí shop**: quảng cáo %, đóng gói, ship shop chịu, tỷ lệ hoàn. Hiện mọi `max_cost` chỉ là trần.
3. **Nhu cầu nhiều thời điểm**: cần ≥ 2 lần chụp "đã bán" (đã hẹn đo lại Lazada khoảng 8–22/12/2026) và nguồn Shopee.
4. **Dữ liệu cạnh tranh có cấu trúc**: số shop, phân bố doanh số, shop Mall/chính hãng.
5. **Xu hướng** cho công tắc, khóa và 9 sản phẩm khác.
6. **Hóa đơn VAT Hunonic, MOQ/thanh toán Rạng Đông** (đang chờ anh).

### Lệch tài liệu / code (đề xuất sửa, chờ anh chốt)
| # | Lệch | Đề xuất |
|---|---|---|
| D1 | Chấm điểm v1 là 7 tiêu chí thang 0–10; spec mới là 5 nhóm thang 0–100 | Thêm `scoring_weights` **v2** (5 nhóm), giữ v1 cho lịch sử. Khác biệt hóa gộp vào Cạnh tranh; Phù hợp gộp vào Tài chính |
| D2 | Kiến trúc mục 4.3 nói biến thể là sản phẩm riêng (`variant_of`); dữ liệu thật dùng `variant_key` trên listing | Đơn vị đánh giá = `product_id` + `variant_key`. Cập nhật mục 4.3 cho khớp thực tế |
| D3 | Không có giai đoạn `seasonal` | Chưa thêm; ghi "chưa phát hiện mùa vụ" là giới hạn đã biết |
| D4 | Kind `evaluation` không có trong schema (ghi bằng `validate_kind=False`) | Thêm vào `schemas/entities.json` |
| D5 | Chưa có skill Agent 03, 04 | Agent 07 không chờ; Gate Nhu cầu sẽ báo thiếu và "việc tiếp theo" chỉ ra agent cần chạy |
| D6 | Orchestrator gọi `aph-<agent>`, tên thật là `dtm-*`; orchestrator chưa lưu vào tài khoản | Sửa tên trong orchestrator khi làm Agent 07 |
| D7 | Agent 07 cần `max_cost` từ PR #7 chưa merge | Merge PR #7 trước, hoặc làm Agent 07 trên nhánh nối tiếp |
| D8 | `confidence.json` chỉ tính bản ghi profit `complete`, nên `max_cost` cho độ tin cậy Tài chính = 0 | Thêm ánh xạ: `max_cost` partial tính như `estimated` (0.4) |
| D9 | `score()` biến điểm null thành 0, trái quy tắc "UNKNOWN không phải 0" | Trong v2: nhóm null bị loại khỏi tổng có trọng số, báo % trọng số đã phủ. Giữ hành vi v1 cho v1 |

## 4. Thành phần dùng lại

Dùng nguyên: kho và `add`/`reindex`/`retraction`, `compute_confidence`, `trend_assess`, `vendor_assessment`, `price_stats`, `max_unit_cost`, `discount_needed`, `coverage`, `show`, quy tắc kiểm tra `evidence_excerpt`, quy ước báo cáo (Dữ liệu quan sát / Nhận định của AI / Không truy cập được).

Mở rộng nhỏ: `score()` thêm nhánh v2 (null không thành 0), `gate()` thêm 4 trạng thái mới ánh xạ sang `research_status`, `confidence.json` thêm ánh xạ `max_cost`.

Thêm mới: lệnh `evaluate`, `eval-diff`, skill `dtm-opportunity-evaluator`.

## 5. Thiết kế chấm điểm và Risk Gate theo dữ liệu thật

### Chấm điểm (v2, 0–100 mỗi nhóm, trọng số cấu hình được)

| Nhóm | Trọng số | Ai chấm | Cách tính |
|---|---|---|---|
| Nhu cầu | 25 | Công cụ đặt **khung trần** theo bằng chứng; AI chọn điểm trong khung và dẫn mã bản ghi | Chỉ tín hiệu chú ý (search, CSI): trần 40. 1 lần chụp doanh số: trần 60. ≥ 2 thời điểm hoặc ≥ 2 sàn: trần 100. Không có gì: null |
| Xu hướng | 15 | Công cụ | growing 80, trending 90, emerging 65, stable 50, saturated 30, declining 15, unknown → null |
| Cạnh tranh | 20 | Công cụ đặt khung; AI chọn trong khung | Từ số tin đăng cùng model, độ phân tán giá, giá niêm yết của hãng (ví dụ BLE mọi shop 395.000đ = cạnh tranh giá thấp nhưng biên lãi phụ thuộc chiết khấu). < 3 tin: null |
| Nhà cung cấp | 15 | Công cụ | `vendor_assessment` tốt nhất trong các NCC của đúng model: điểm/12 × 80, +20 nếu có `dealer_quote`. Không có NCC: null |
| Tài chính | 25 | Công cụ | Có profit `complete` với giá nhập thật: điểm theo biên lãi ròng. Chỉ có `max_cost` trần: điểm theo % chiết khấu cần xin, **trần 50** và ghi "ước tính". Không có: null |

Mỗi nhóm trả về: `score` (hoặc null), `reason`, `evidence_ids`, `sources`, `confidence`, `basis` (`observed` / `estimated` / `ai_judgment`).

Tổng điểm = tổng có trọng số của các nhóm **khác null**, chuẩn hóa lại, kèm `weight_coverage` (ví dụ "phủ 60% trọng số"). Thiếu dữ liệu hiển thị "Chưa chấm (thiếu dữ liệu)", tách khỏi "Điểm thấp".

### Risk Gates

| Gate | Đạt | Trượt cứng (NO GO) | Chưa đủ (NEED MORE DATA) |
|---|---|---|---|
| 1. Nhu cầu | có doanh số quan sát ≥ 2 thời điểm hoặc ≥ 2 sàn | doanh số quan sát gần 0 trên nhiều sàn | chỉ có tín hiệu chú ý hoặc 1 lần chụp |
| 2. Nhà cung cấp | ≥ 1 NCC mức medium trở lên cho đúng model | mọi NCC thấp và không pháp nhân | chưa có NCC đánh giá |
| 3. Tài chính | profit `complete` với giá nhập thật, lãi ≥ mục tiêu | giá nhập thật > trần giá nhập (lãi 10%) | chỉ có trần (không được nói "có lãi") |
| 4. Data Confidence | ≥ 60 | không áp dụng | < 60 |
| 5. Nhận diện sản phẩm | mọi bằng chứng cùng `variant_key` | không áp dụng | bằng chứng trộn model (ví dụ Hunonic WiFi, BLE, Tuya Zigbee) hoặc "nhiều mẫu" như khóa vân tay |

### Trạng thái (không quyết bằng điểm riêng)
- **NO GO**: có gate trượt cứng → `rejected`, kèm lý do.
- **NEED MORE DATA**: Gate Nhu cầu hoặc Nhận diện chưa đủ, hoặc ≥ 2 nhóm null, hoặc Data Confidence < 60 → `research_more`.
- **GO**: mọi gate đạt, điểm ≥ 75, Data Confidence ≥ 60 → `qualified_opportunity`.
- **CONDITIONAL GO**: còn lại; liệt kê điều kiện (ví dụ "đạt nếu Hunonic chiết khấu ≥ 35%") → `research_more`.

### Bản ghi `evaluation`
Append-only, gồm: `product_id`, `variant_key`, `weights_version`, 5 nhóm (như trên), 5 gate, `status`, `conditions`, `next_actions` (thiếu gì → agent nào hoặc anh), `input_snapshot` (mã và thời điểm mọi bản ghi đầu vào). `eval-diff` so hai lần đánh giá: điểm, trạng thái, gate nào đổi và vì bản ghi mới nào.

### Dự kiến lần chạy đầu (từ dữ liệu hiện có, chưa chạy)
Cả 7 model của 3 sản phẩm nhiều khả năng **NEED MORE DATA** (Gate Nhu cầu chưa đủ, Tài chính chỉ có trần). Khóa vân tay còn trượt Gate Nhận diện. 9 sản phẩm còn lại cũng NEED MORE DATA. Báo cáo sẽ hữu ích ở phần "việc tiếp theo": đo lại doanh số, xin báo giá đại lý, thu giá Shopee.

## 6. Kế hoạch triển khai (sau khi anh xác nhận)

| Giai đoạn | Việc | Thay đổi thành phần dùng chung |
|---|---|---|
| C. Nền | Merge PR #7 (hoặc nhánh nối tiếp). Thêm kind `evaluation` vào schema, `scoring_weights` v2, ánh xạ `max_cost` trong `confidence.json` | Có (D1, D4, D8): cần anh duyệt |
| D. Công cụ | Lệnh `evaluate --product [--variant]` và `eval-diff`; nhánh v2 trong `score()`; 4 trạng thái trong `gate()` | Có (D9) |
| E. Skill, tài liệu, chạy thật | Skill `dtm-opportunity-evaluator`; sửa tên trong orchestrator (D6); cập nhật kiến trúc 4.3 (D2) và lộ trình; chạy trên 3 sản phẩm; báo cáo tiếng Việt 16 mục mỗi sản phẩm + bảng xếp hạng + việc tiếp theo | Tài liệu |

7 ca kiểm thử (thư mục tạm, không ghi kho thật):
1. Nhóm thiếu dữ liệu ra null, không ra 0, và bị loại khỏi tổng có trọng số.
2. Chỉ có tín hiệu chú ý thì Nhu cầu không vượt trần 40 và Gate Nhu cầu là "chưa đủ".
3. Chỉ có `max_cost` trần thì Tài chính tối đa 50, `basis=estimated`, không bao giờ ra GO.
4. Giá nhập thật vượt trần thì NO GO.
5. Bằng chứng trộn `variant_key` thì Gate Nhận diện chặn.
6. Điểm cao nhưng một gate chưa đủ thì không ra GO.
7. Hai lần đánh giá: bản ghi cũ giữ nguyên, `eval-diff` chỉ ra đúng thay đổi và bản ghi gây ra nó.

## Quyết định của anh (24/11/2026 15:27)

1. Trọng số v2: Nhu cầu 25, Xu hướng 15, Cạnh tranh 20, Nhà cung cấp 15, Tài chính 25. Giữ v1.
2. Đơn vị đánh giá: `product_id` + `variant_key`, không gộp model.
3. PR #7 đã merge.

## 7. Cập nhật Design theo 4 yêu cầu bổ sung

### A. Weight Coverage Gate
- `weight_coverage` = tổng trọng số các nhóm **có điểm** (khác null), 0–100%.
- Điểm tổng hiển thị = điểm đã chuẩn hóa trên phần trọng số có dữ liệu (Σ w·điểm / Σ w của nhóm có điểm), luôn đi kèm `weight_coverage`, ví dụ "62 điểm, phủ 60% trọng số".
- **GO cần `weight_coverage` ≥ 80%**. Dưới 80% thì không GO dù điểm ≥ 75.

### B. Cạnh tranh: chiều điểm
Điểm cao = cơ hội cạnh tranh tốt cho shop anh:
80–100 cơ hội cạnh tranh tốt · 60–79 có khả năng cạnh tranh · 40–59 cạnh tranh khó · 0–39 cạnh tranh rất khó.

Điểm là trung bình các thành phần có bằng chứng (tin đăng cùng model, cùng sàn):
| Thành phần | Bằng chứng | Quy tắc |
|---|---|---|
| Mật độ đối thủ | số shop khác nhau bán đúng model | ≤ 3 shop: 75 · 4–7: 60 · 8–15: 45 · > 15: 30. **Nhiều đối thủ là điểm thấp hơn**, không phải cơ hội |
| Phân bố giá | P25, trung vị, thấp nhất | Có shop bán dưới 70% trung vị (phá giá): 35. Giá niêm yết cố định (chênh P25–P75 < 3% trung vị): 50, vì không cạnh tranh được bằng giá, lãi phụ thuộc chiết khấu đại lý. Còn lại 65 |
| Mức tập trung doanh số | nhãn "đã bán" từng tin | Tin dẫn đầu > 70% tổng: 35 · > 50%: 50 · còn lại 70. Không có nhãn bán: bỏ thành phần này |
| Lợi thế đối thủ | tỷ lệ tin của shop hãng, nhà phân phối, Official, Mall | ≥ 50%: 35 · ≥ 25%: 50 · còn lại 65 |
| Khả năng khác biệt | điểm yếu đối thủ (`weaknesses`), phàn nàn / nhu cầu chưa đáp ứng gắn với sản phẩm | Có bằng chứng: 70. **Không có: điểm nhóm Cạnh tranh tối đa 70** và ghi "chưa có bằng chứng khác biệt (cần Agent 04)" |

< 3 tin đăng cùng model: nhóm Cạnh tranh = null. Shop của anh (Điện Toàn Mỹ) bị loại khỏi đối thủ.

### C. Xu hướng UNKNOWN
- `unknown` → null ("Chưa chấm, thiếu dữ liệu"), không phải điểm thấp, không bị tự suy thành growing hay stable.
- Ánh xạ khi có kết luận: emerging 70, growing 80, trending 85, **stable 65**, saturated 35, declining 20. Ổn định không bị phạt.
- Xu hướng **không phải Gate**: không cần tăng trưởng mới được GO. Thiếu riêng Xu hướng chỉ làm `weight_coverage` còn 85%, vẫn đủ ngưỡng GO.

### D. Product Identity
Mỗi bản ghi đầu vào được xếp vào một trong 4 loại, ghi trong `identity` của bản đánh giá:
| Loại | Ý nghĩa | Dùng? |
|---|---|---|
| `match` | đúng `product_id` và đúng `variant_key` | Có |
| `product_scope` | dữ liệu vốn ở cấp sản phẩm: xu hướng, lượt tìm kiếm, vấn đề khách hàng, đánh giá pháp nhân NCC | Có, ghi rõ "cấp sản phẩm, chung mọi model" |
| `other_model` | `variant_key` khác | Không |
| `unassigned` | dữ liệu theo model (tin đăng, NCC, giá, trần giá nhập, lợi nhuận) nhưng không xác định được model | Không |

Gán model cho bản ghi cũ không sửa bản ghi: thêm kind **`variant_link`** (append-only) gồm `product_id`, `variant_key`, `target` (id bản ghi), `reason`, `evidence_excerpt`. Công cụ từ chối nếu đoạn trích không có nguyên văn trong chính bản ghi đích (tên, tiêu đề, ghi chú, đoạn trích), hoặc bản ghi đích đã có `variant_key` khác. Bản ghi mới có thể ghi thẳng `variant_key` (thêm trường tùy chọn này cho `supplier`, `demand`, `profit`).

Gate Identity = **chưa đủ dữ liệu** khi:
- model có < 3 tin đăng gắn đúng `variant_key`;
- giá các tin cùng model trải quá rộng (P75/P25 > 1,4), dấu hiệu một `variant_key` đang gộp nhiều mẫu;
- sản phẩm có NCC chưa gán model và không NCC nào gắn đúng model này.

Sửa kèm (lỗi nhận diện ở công cụ Agent 06): `build_profit_inputs` đang chọn giá nhập rẻ nhất trong **mọi** NCC của sản phẩm, kể cả khác model. Khi có `--variant`, chỉ lấy NCC gắn đúng model. Lệnh `profit` thêm `--variant` để bản ghi lợi nhuận mang model.

### Trạng thái (bản cuối)
1. Có Gate trượt cứng → **NO GO**. Trượt cứng gồm: nhu cầu yếu đã xác nhận ở ≥ 2 nguồn hoặc ≥ 2 thời điểm (tổng đã bán < 30); giá nhập thật vượt trần giá nhập lãi 10%; lãi ròng âm với đủ chi phí.
2. Gate Nhu cầu hoặc Identity chưa đủ, hoặc ≥ 2 nhóm null, hoặc Data Confidence < 60 → **NEED MORE DATA**.
3. Mọi Gate đạt, điểm ≥ 75, `weight_coverage` ≥ 80% → **GO**.
4. `weight_coverage` ≥ 80% mà điểm < 50 → **NO GO** (điểm thấp khi dữ liệu đã đủ).
5. Còn lại → **CONDITIONAL GO**, kèm danh sách điều kiện.

Ánh xạ `research_status` (chỉ là đề xuất, không tự ghi quyết định): GO → qualified_opportunity; CONDITIONAL GO, NEED MORE DATA → research_more; NO GO → rejected.

### Kiểm thử bổ sung
Ngoài 7 ca ở mục 6: điểm ≥ 75 nhưng `weight_coverage` < 80% thì không GO; xu hướng unknown ra null và stable vẫn GO được; nhiều đối thủ không làm tăng điểm cạnh tranh; dữ liệu khác model bị loại, NCC chưa gán model làm Gate Identity chưa đủ; `variant_link` từ chối đoạn trích không có trong bản ghi đích.
