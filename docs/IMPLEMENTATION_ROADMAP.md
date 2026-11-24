# AI PRODUCT HUNTER — IMPLEMENTATION ROADMAP

> Phiên bản: 0.4 · Cập nhật: 2026-11-24 11:40 (giờ VN) · Đi kèm: `SYSTEM_ARCHITECTURE.md` v0.4, `PHASE_1_AUDIT.md`
>
> **Quy ước trạng thái** (dùng thống nhất trong toàn bộ tài liệu):
> - ✅ **Hoàn thành, đã kiểm thử**: có kiểm thử tự động hoặc kiểm thử thật, ghi rõ bằng chứng.
> - 🧪 **Hoàn thành, chưa kiểm thử**: đã viết xong nhưng chưa có kiểm thử chứng minh nó chạy đúng.
> - ⏳ **Đang chờ người dùng duyệt**: việc của Claude đã xong, cần người dùng bấm duyệt, merge hoặc trả lời.
> - 🔶 **Một phần**: làm được một phần, phần thiếu ghi rõ.
> - ⬜ **Chưa làm**.
>
> Nguyên tắc: chỉ ghi ✅ khi có kiểm thử; không dùng dữ liệu giả để chứng minh hệ thống chạy được.

---

## 1. Trạng thái tổng quan

| Phase | Mục tiêu | Trạng thái | Ghi chú |
|---|---|---|---|
| 1 | Kiến trúc và kiểm tra môi trường | ✅ | Đã kiểm toán (`PHASE_1_AUDIT.md`), đã kiểm chứng G1–G3 |
| 2 | Core system | 🔶 | Code, công cụ, rules đã merge (PR #1) và có 44 unit test đạt. Còn thiếu: kiểm thử khô orchestrator (🧪), người dùng lưu skill (⏳), các biểu phí còn `missing` |
| 2b | Điều chỉnh trước Phase 3 (yêu cầu 2026-11-24 11:17) | ⏳ | Nằm trong PR #2, chờ người dùng duyệt |
| 3 | 7 Agent, làm lần lượt | ⬜ | Đề xuất bắt đầu bằng Agent 01 Market Hunter, **không** làm đồng loạt |
| 4 | Báo cáo và dashboard | ⬜ | |
| 5 | Vận hành bán tự động theo lịch | ⬜ | Lịch cloud đã kiểm chứng: **không** dùng được trình duyệt PC |
| 6 | Kiểm thử tổng thể | ⬜ | |

---

## 2. Quyết định của người dùng (đã có)

| # | Quyết định | Ngày | Ảnh hưởng |
|---|---|---|---|
| D1 | Repo GitHub `lengockhoa90/ai_product_hunter` | 2026-11-24 | Code/skills/rules ở repo, dữ liệu ở `/mnt/project-files/ai-product-hunter/data` |
| D2 | Shopee: người dùng đã đăng nhập trong trình duyệt Claude; **tự giải CAPTCHA** | 2026-11-24 | Shopee chỉ đọc trong phiên thu thập có người |
| D3 | Tìm nguồn hàng **trong nước** | 2026-11-24 | 1688/Alibaba chỉ dùng tham khảo giá gốc |
| D4 | Vốn thử nghiệm 10.000.000 đ; kênh chính Shopee + TikTok Shop | 2026-11-24 | `config/business_profile.json` |
| D5 | Vận hành **bán tự động** | 2026-11-24 | Lịch cloud: WebSearch, chấm điểm, báo cáo, hàng đợi; thu thập trình duyệt trong thread người dùng mở |
| D6 | Cho phép thêm trends.google.com, www.alibaba.com, s.1688.com vào mạng cloud | 2026-11-24 | Đã thử, đều bị chặn chống bot; 3 host vẫn trong danh sách, không dùng |
| D7 | Thêm Problem-based Discovery, nâng Demand/Confidence/Profit, nguồn thay thế Shopee; làm Agent 01 trước | 2026-11-24 | Mục 4 và mục 5 |

**Câu hỏi còn mở cho người dùng:**
- Q1. Shop Shopee và TikTok Shop của anh là **Mall** hay **shop thường**? Biểu phí khác nhau, nên khi chưa biết thì phí chưa được áp dụng.
- Q2. Lưu skill `aph-orchestrator` trên thẻ đề xuất. Lần chạy theo lịch lúc 11:06 cho thấy skill chưa được lưu.

---

## 3. PHASE 2 — CORE SYSTEM (chi tiết trạng thái)

| # | Hạng mục | Trạng thái | Bằng chứng / phần còn thiếu |
|---|---|---|---|
| 2.1 | Kiểm thử truy cập nguồn | ✅ | `docs/SOURCE_ACCESS_MATRIX.md` (kiểm thử thật 2026-11-24). Chưa thử: Taobao, Tiki, web TikTok Shop, diễn đàn VN, trang Google Trends mới |
| 2.2 | Schemas `schemas/entities.json` | ✅ | Công cụ kiểm tra mọi bản ghi theo schema; test `TestSourceRules` |
| 2.3 | `tools/aph.py` | ✅ | Lệnh: `init`, `new-run`, `finish-run`, `match`, `add-product`, `relate`, `add`, `log-access`, `profit`, `confidence`, `score`, `gate`, `set-status`, `show`, `list`, `duplicates`, `reindex`, `problems`*, `coverage`* (*thêm ở 2b) |
| 2.4 | Chống trùng lặp | ✅ | Test trùng tên khác ngôn ngữ, biến thể WiFi/Zigbee (trả `needs_review`, không tự gộp), tên khác hẳn. Run thật: thêm lại sản phẩm bằng tên en/zh thì nhận ra sản phẩm cũ |
| 2.5 | Rules | ✅ | `rules/research_rules.md`, `rules/scoring_rules.md` |
| 2.6 | Config | ✅ | Trọng số v1, độ tin cậy v2, danh mục, ngân sách độ sâu, hồ sơ kinh doanh, nguồn thay thế |
| 2.7 | Biểu phí có nguồn | 🔶 | Có nguồn: Phí Xử Lý Giao Dịch Shopee 6% và Voucher Xtra 5,5% (bài dành cho **Shopee Mall**, ngày 14/05/2026). `missing`: phí cố định Shopee theo ngành hàng, toàn bộ phí TikTok Shop, phí cho shop thường |
| 2.8 | Skill `aph-orchestrator` | 🧪 + ⏳ | Đã viết; **chưa kiểm thử khô** với 7 câu lệnh mẫu; chờ người dùng lưu skill |
| 2.9 | `CLAUDE.md` | ✅ | Được nạp khi repo có trong phiên (lần chạy theo lịch thấy repo) |
| 2.10 | Run thật tối thiểu | ✅ | 1 sản phẩm thật (cảm biến rò rỉ nước Zigbee), 2 observation, 2 NCC tham khảo, 1 lỗi CAPTCHA Shopee. 2 observation này ghi trước khi có quy tắc `evidence_excerpt` |
| 2.11 | Sửa theo kiểm toán (G4–G6) | ✅ | Kiến trúc v0.3, `evidence_excerpt` (test `TestEvidenceExcerpt`), sửa hiệu năng `list` (test 2.000 sản phẩm) |

---

## 4. PHASE 2b — ĐIỀU CHỈNH TRƯỚC PHASE 3 (yêu cầu 2026-11-24 11:17)

| # | Yêu cầu | Đã làm | Trạng thái |
|---|---|---|---|
| R1 | Problem-based Opportunity Discovery | Kiến trúc mục 3.6. Bản ghi `problem` (chống trùng theo `problem_key`/độ giống câu) và `solution` (full/partial/bundle_component). Bằng chứng gắn `problem_ref` được tính cho mọi sản phẩm giải quyết vấn đề đó. Lệnh `aph.py problems` | ✅ logic (test `TestProblems`) · ⬜ chưa chạy với dữ liệu thật (làm ở Agent 01) |
| R2 | Demand Analyst theo vấn đề | Kiến trúc mục 3.7: 8 loại tín hiệu (thêm `question`, `alternative_solution`, `price_acceptance`), `demand` gắn được với `problem_ref` | ✅ schema/logic · ⬜ skill Agent 03 chưa viết |
| R3 | Data Confidence 4 lớp, không dựa riêng vào Trends/lượt xem | Kiến trúc mục 4.4. `confidence.json` v2: lớp trực tiếp / bên thứ ba / ước tính / suy luận, đếm theo lớp. Chỉ có tín hiệu chú ý thì độ tin cậy nhu cầu ≤ 0,3. `listing_count` không còn tính là nhu cầu | ✅ (test `TestDemandNotAttentionOnly`). Trên dữ liệu thật: độ tin cậy nhu cầu của cảm biến rò rỉ nước giảm từ 0,727 xuống **0,3** (`attention_only=true`), Data Confidence từ 37,9 xuống 29,4 |
| R4 | Profit chỉ dùng biểu phí có nguồn, có ngày, đúng ngành | Kiến trúc mục 4.5. `aph.py profit --platform` kiểm tra nguồn, ngày, sàn, ngành hàng, loại shop, hạn; sai thì `incomplete` + `unverified_fees` | ✅ (test `TestFeeVerification`). Với dữ liệu hiện có, mọi phép tính lợi nhuận đều sẽ ra `incomplete` cho tới khi có phí cố định theo ngành và trả lời Q1. Đây là kết quả đúng mong muốn |
| R5 | Nhiều nguồn thay thế Shopee | Kiến trúc mục 3.8. `config/source_fallbacks.json`, lệnh `aph.py coverage` | ✅ (test `TestCoverage`). Trên dữ liệu thật: chỉ "chú ý" và "nguồn cung" đủ phủ; giá VN, nhu cầu VN chưa có; Shopee ghi nhận `captcha` |
| R6 | Đồng bộ trạng thái tài liệu | Roadmap viết lại với quy ước trạng thái mới; sửa tên lệnh `aph.py` cho khớp code; bỏ `merge_candidate` (code dùng `needs_review`) | ✅ |
| R7 | Cập nhật tài liệu | `SYSTEM_ARCHITECTURE.md` v0.4, roadmap này | ⏳ chờ duyệt PR #2 |

Tương thích: không đổi tên hay xoá trường nào đã có. `product_id` của `observation`/`demand` chuyển từ bắt buộc sang "cần `product_id` hoặc `problem_ref`". 29 test cũ vẫn đạt; tổng 44 test đạt.

---

## 5. PHASE 3 — 7 AGENT, LẦN LƯỢT

Mỗi Agent là `skills/aph-<agent>/SKILL.md` gồm: Mục tiêu · Input · Output · Quy trình · Công cụ · Tiêu chí hoàn thành · Xử lý lỗi. **Làm từng Agent, kiểm thử thật xong mới sang Agent sau.**

| Thứ tự | Agent | Trạng thái | Tiêu chí kiểm thử thật |
|---|---|---|---|
| 3.1 | **01 Market Hunter** (2 chế độ: theo danh mục và problem-first) | 🧪 Skill `aph-market-hunter` đã viết. Chế độ B chạy thật 1 lần (run_20261124T052140_58ec): 1 vấn đề, 2 giải pháp, chạy lại không trùng; **chưa đạt** ≥ 3 nguồn cho vấn đề. Chế độ A chạy thật 1 lần (run_20261124T053822_3b7c): 10 sản phẩm, mỗi sản phẩm có nguồn + ngày + đoạn trích, chạy lại không trùng; chưa có bằng chứng "mới trên thị trường" cho sản phẩm nào. ⏳ Đang chờ người dùng duyệt PR | Xem mục 5.1 |
| 3.2 | 06 Profit Analyst | ⬜ | Lấy được ít nhất 1 biểu phí cố định theo ngành có nguồn; `profit` đúng với bảng tính tay; thiếu phí thì `incomplete` |
| 3.3 | 07 Opportunity Evaluator | ⬜ | Điểm, Data Confidence, trần 74, nhãn 5 dạng cơ hội cho các sản phẩm của 3.1 |
| 3.4 | 03 Demand Analyst | ⬜ | Với 1 vấn đề thật: ≥ 3 nguồn độc lập; có tín hiệu ngoài tín hiệu chú ý |
| 3.5 | 02 Trend Hunter | ⬜ | ≥ 2 mốc thời gian, phân loại Emerging→Declining; chuyển sang trang Google Trends mới |
| 3.6 | 05 Supplier Hunter | ⬜ | ≥ 3 NCC trong nước có giá, hoặc ghi rõ "cần báo giá" |
| 3.7 | 04 Competitor Analyst | ⬜ | ≥ 2 nền tảng cho giá/cạnh tranh VN; Shopee khi người dùng giải CAPTCHA |
| 3.8 | Tích hợp: 1 deep-dive thật | ⬜ | Đo thời gian và số trang (G7); mọi nhận định có trích dẫn |

### 5.1 Đề xuất chi tiết: Agent 01 Market Hunter

| Mục | Nội dung |
|---|---|
| Mục tiêu | Phát hiện sản phẩm **và vấn đề khách hàng** đáng nghiên cứu; không chỉ liệt kê hàng bán chạy |
| Chế độ A: theo danh mục | Từ danh mục ưu tiên (`config/categories.json`) → sản phẩm mới ở thị trường nguồn (1688/Alibaba/website nhà sản xuất, WebSearch) → kiểm tra mức độ có mặt tại VN (Lazada, Google VN, TikTok; Shopee nếu người dùng giải CAPTCHA) |
| Chế độ B: problem-first | Từ một tình huống ("rò rỉ nước khi đi vắng") → tìm câu hỏi/phàn nàn trên nguồn VN → ghi `problem` → tìm giải pháp hiện có và khoảng trống → ghi `solution`, kể cả bộ sản phẩm |
| Input | Danh mục hoặc tình huống, độ sâu (`quick` mặc định), số lượng mong muốn |
| Output | `product` (chống trùng), `problem`, `solution`, `observation` có `evidence_excerpt`, `new_to_market_evidence`, `log-access` cho nguồn bị chặn |
| Công cụ | Trình duyệt Claude trên PC (trong thread), WebSearch, `aph.py` |
| Tiêu chí hoàn thành | Chế độ A: ≥ 10 sản phẩm thật nhóm nhà thông minh, mỗi sản phẩm có ≥ 1 nguồn + ngày + đoạn trích, phân biệt "mới trên thị trường" và "mới với người nghiên cứu". Chế độ B: ≥ 1 vấn đề thật có ≥ 3 nguồn, ≥ 2 giải pháp được ghép. Chạy lại lần 2 không tạo bản ghi trùng |
| Xử lý lỗi | CAPTCHA/đăng nhập → dừng nguồn đó, `log-access`, dùng nguồn trong `source_fallbacks.json`; PC không liên kết → chỉ chạy phần WebSearch và báo rõ |
| Kiểm thử trước khi viết skill | Thử truy cập diễn đàn VN và hỏi đáp Lazada (nguồn vấn đề chưa kiểm thử) |
| Cần người dùng | PC bật và đang liên kết khi chạy thử; giải CAPTCHA Shopee nếu muốn có Shopee trong lần thử |

---

## 6. PHASE 4 — BÁO CÁO

| # | Hạng mục | Trạng thái |
|---|---|---|
| 4.1 | Daily Report (từ kho, có mục "không truy cập được" và "không có dữ liệu Shopee") | ⬜ |
| 4.2 | Weekly Report (Top 10, điểm + Data Confidence, thay đổi so với tuần trước) | ⬜ |
| 4.3 | Deep Research 14 mục, trích dẫn `[obs_id]` → URL + ngày | ⬜ |
| 4.4 | Watchlist + Opportunity Ranking + danh sách vấn đề (Artifact) | ⬜ |
| 4.5 | Cập nhật trạng thái kinh doanh và Outcome bằng câu lệnh tự nhiên | 🧪 (`set-status` có, chưa có skill) |

---

## 7. PHASE 5 — VẬN HÀNH BÁN TỰ ĐỘNG

| # | Việc | Trạng thái |
|---|---|---|
| 5.1 | Kiểm thử lịch cloud có dùng được trình duyệt PC không | ✅ Không (lần chạy 2026-11-24 11:06, task thử đã xoá) |
| 5.2 | Lịch `daily-prep` (WebSearch, chấm lại điểm, Daily Report phần kho, hàng đợi) | ⬜ cần người dùng đồng ý tạo lịch |
| 5.3 | Phiên `daily_collect` trong thread người dùng mở | ⬜ |
| 5.4 | Lịch `weekly-review` | ⬜ |
| 5.5 | Xử lý lỗi lịch: heartbeat, chống chạy chồng, chạy lại 1 lần, báo khi trượt 2 lần | ⬜ (đã thiết kế ở kiến trúc mục 6) |
| 5.6 | ~~Dự phòng Cowork desktop~~ | Bỏ (tác vụ trên PC không ghi được vào thư mục dự án) |

---

## 8. PHASE 6 — KIỂM THỬ TỔNG THỂ

`docs/TEST_REPORT.md` với kết quả thật cho: tìm sản phẩm, tìm vấn đề, xác minh dữ liệu, đối thủ, nhà cung cấp, lợi nhuận, phối hợp Agent, lưu/cập nhật, trùng lặp, báo cáo, vận hành theo lịch. Đối chiếu 12 tiêu chí nghiệm thu (mục XIV của đặc tả).

---

## 9. Kiểm toán và việc còn mở

| # | Việc (theo `PHASE_1_AUDIT.md`) | Trạng thái |
|---|---|---|
| G1 | Kiểm thử lịch chạy | ✅ |
| G2 | Truy cập trực tiếp từ cloud | ✅ (bị chặn) |
| G3 | Phương án Shopee | ✅ (người dùng tự giải CAPTCHA) |
| G4 | Kiến trúc v0.3 | ✅ |
| G5 | `evidence_excerpt` | ✅ |
| G6 | Hiệu năng `list` | ✅ |
| G7 | Đo chi phí một deep-dive thật | ⬜ (Phase 3.8) |
| G8 | Kiểm thử Taobao, Tiki, web TikTok Shop, Google Trends trang mới, diễn đàn VN | 🧪 Diễn đàn VN đã thử 2026-11-24: otofun ✅, tinhte qua Google, voz ❌ Cloudflare, Facebook ❌ đăng nhập. Taobao, Tiki, TikTok Shop web, Trends mới ⬜ |

---

## 10. Cấu trúc thư mục (hiện tại)

```
ai_product_hunter/                     # GitHub repo
├── CLAUDE.md
├── README.md
├── docs/
│   ├── SYSTEM_ARCHITECTURE.md         # v0.4
│   ├── IMPLEMENTATION_ROADMAP.md      # v0.4
│   ├── SOURCE_ACCESS_MATRIX.md
│   └── PHASE_1_AUDIT.md
├── skills/aph-orchestrator/SKILL.md   # các skill Agent thêm lần lượt ở Phase 3
├── rules/{research_rules,scoring_rules}.md
├── schemas/entities.json
├── config/{scoring_weights,confidence,categories,depth_budgets,business_profile,source_fallbacks}.json
├── tools/aph.py
└── tests/test_aph.py

/mnt/project-files/ai-product-hunter/  # không commit
├── data/{products,records/YYYY/MM,runs,assumptions}/ + index.sqlite
├── audit/                             # kết quả kiểm thử lịch chạy
└── reports/{daily,weekly,deep}/       # Phase 4
```
