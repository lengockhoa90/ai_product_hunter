# AI PRODUCT HUNTER — SYSTEM ARCHITECTURE

> Phiên bản: 0.3 (điều chỉnh theo `PHASE_1_AUDIT.md` và quyết định vận hành **bán tự động** của người dùng) · Ngày: 2026-11-24
> Thay đổi so với 0.1: gộp bản ghi theo run vào `records/` (0.2); mô hình vận hành bán tự động, bỏ dự phòng Cowork desktop, quy tắc một người ghi, phân tầng theo dõi, xử lý lỗi lịch, bắt buộc `evidence_excerpt`, tách việc Demand/Competitor (0.3).
> Phạm vi: kiến trúc tổng thể, phối hợp Agent, mô hình dữ liệu, lưu trữ, lập lịch, giới hạn kỹ thuật.

---

## 0. Tóm tắt quyết định

| Chủ đề | Quyết định |
|---|---|
| Nơi điều phối | Dự án Claude (project chat + các thread Claude Code chạy trên cloud). Người dùng ra lệnh bằng ngôn ngữ tự nhiên ở project chat, mỗi nhiệm vụ nghiên cứu chạy trong một thread. |
| Hình thức Agent | Mỗi Agent là một **Skill** (quy trình + quy tắc + đầu vào/đầu ra), cùng một phiên Claude thực hiện tuần tự. Không dùng subagent mặc định (tốn token); chỉ dùng khi người dùng yêu cầu nghiên cứu song song. |
| Bộ điều phối | Skill `aph-orchestrator`: hiểu yêu cầu, chọn Agent và độ sâu, chạy cổng kiểm tra giữa các bước, ghi trạng thái. |
| Thu thập dữ liệu web | (1) **Trình duyệt Claude trên PC người dùng**, chỉ dùng được trong thread có người dùng và PC đã liên kết; (2) **WebSearch**, dùng được cả trong lịch chạy. Truy cập trực tiếp từ cloud tới Google Trends/Alibaba/1688 đã thử và bị chặn chống bot (429/punish). |
| Tính toán | Script Python thuần (`tools/aph.py`) cho chấm điểm, lợi nhuận, chống trùng lặp, Data Confidence. Claude không tự cộng trừ bằng tay. |
| Lưu trữ | Thư mục dự án dùng chung `/mnt/project-files/ai-product-hunter/data/` theo mô hình **append-only** (mỗi lần chạy ghi file riêng) + chỉ mục SQLite dựng lại được. Code/skills/rules nên nằm trong một Git repo (cần người dùng tạo). |
| Vận hành | **Bán tự động** (người dùng chọn 2026-11-24): lịch cloud làm WebSearch, chấm điểm lại, báo cáo và lập *hàng đợi thu thập*; mỗi ngày người dùng mở một thread khoảng 10–20 phút để Claude thu thập qua trình duyệt trên PC (người dùng tự giải CAPTCHA Shopee). Lịch chạy chỉ tạo khi người dùng đồng ý. |
| Báo cáo | Markdown trong `reports/` (Daily/Weekly/Deep) + một Artifact dashboard cho Opportunity Ranking/Watchlist ở Phase 4. |
| Không dùng | Claude API, OpenAI API, n8n, dịch vụ AI trung gian, nền tảng automation ngoài, scraping hàng loạt, vượt CAPTCHA/anti-bot. |

---

## 1. Kết quả kiểm tra môi trường (thực tế, 2026-11-24)

Mọi mục dưới đây là kết quả kiểm tra thật trong phiên này, không phải giả định. Mục nào chưa kiểm chứng được ghi rõ.

### 1.1 Container Claude Code (cloud)

| Hạng mục | Kết quả |
|---|---|
| Hệ điều hành | Linux x86_64, phiên tạm thời (container bị thu hồi khi không dùng) |
| Công cụ | Python 3.13.18, SQLite 3.53.1, Node 22, git, jq — đủ để chạy script tính toán và chỉ mục dữ liệu |
| Thư mục dùng chung | `/mnt/project-files/` (network mount, tồn tại lâu dài, mọi thread cùng đọc/ghi, "ghi sau thắng") |
| Truy cập mạng trực tiếp | **Bị chặn (HTTP 403 từ proxy)**: shopee.vn, tiktok.com, google.com, trends.google.com, 1688.com, alibaba.com |
| WebSearch | **Hoạt động**. Chỉ mục thiên về Mỹ/quốc tế; kết quả thử với "cảm biến rò rỉ nước zigbee shopee" trả về chủ yếu trang quốc tế (sonoff.tech, pimoroni, ebay), ít trang Việt Nam → yếu với dữ liệu sàn VN |
| Git repo | Chưa có repo nào gắn vào dự án |
| Connector | Chỉ có Canva (không liên quan nghiên cứu) |

### 1.2 Máy người dùng (Windows, đã liên kết với thread)

| Hạng mục | Kết quả |
|---|---|
| Trình duyệt tích hợp (Claude Browser) | **Hoạt động** khi Claude desktop app đang mở |
| Shopee search | Trang chuyển ngay sang `shopee.vn/verify/traffic` (cơ chế chống bot). **Không được vượt qua.** → Không thu thập tự động Shopee được |
| 1688 search | Trang tải được. Từ khóa mã hóa UTF-8 → 0 kết quả (1688 cần mã GBK). Với từ khóa GBK, trang treo ở trạng thái loading → **chưa kiểm chứng**, có thể cần đăng nhập |
| Google Trends | **Tải được** trang Khám phá (geo=VN, 90 ngày). Trích xuất số liệu biểu đồ cần làm và kiểm thử ở Phase 2 (skill `trend-hunter-dtm` hiện có đã có quy trình đọc Trends) |
| TikTok | Chưa kiểm tra trong phiên này. Skill `trend-hunter-dtm` ghi rằng trình duyệt đã đăng nhập TikTok và dùng Creator Search Insights — cần xác nhận lại ở Phase 2 |
| Alibaba.com, Taobao | Chưa kiểm tra (Phase 2) |
| Computer use | Có công cụ, chưa cần dùng |
| Thư mục máy | Chưa kết nối thư mục nào. Có thư mục `iot_db`, `My Drive` (Google Drive), `DTM_Video` (dùng bởi skill Trend Hunter hiện có) |

### 1.3 Skills và lập lịch

| Hạng mục | Kết quả |
|---|---|
| Skills tài khoản | `trend-hunter-dtm` (xu hướng nội dung video TikTok cho Điện Toàn Mỹ), 3 skill dựng video DTM, `deep-research`, `skill-creator`, docs/xlsx/pdf/pptx, browser, computer-use |
| Tạo/sửa skill | Chỉ qua thẻ đề xuất (`propose_skills`) để **người dùng duyệt và lưu**. File skill trên đĩa là bản cache chỉ đọc |
| Scheduled tasks (cloud, dự án) | Có công cụ tạo/sửa/xoá. Hiện **0** task. Tạo task cần người dùng đồng ý |
| Cron trong phiên | Chỉ sống trong phiên hiện tại → không dùng cho tự động hóa dài hạn |
| Cowork desktop scheduled tasks | Tồn tại trên máy người dùng nhưng **không liệt kê/điều khiển được từ cloud** → chỉ làm phương án bàn giao thủ công |

### 1.4 Hệ quả thiết kế

1. Mọi thu thập dữ liệu sàn TMĐT/MXH phải đi qua **trình duyệt trên máy người dùng** → PC phải bật và Claude desktop app phải mở khi chạy.
2. Shopee không thu thập tự động được. Dữ liệu Shopee chỉ đến từ: người dùng tự mở trang/hoàn tất xác minh rồi Claude đọc trang đang hiển thị (mức độ nhỏ, không hàng loạt), dữ liệu Seller Center của chính người dùng, hoặc nguồn gián tiếp (WebSearch, bài báo). Mọi trường hợp không truy cập được phải ghi nhận là `access_failed`.
3. Không có API → không có lượt bán/GMV chính xác. Chỉ ghi giá trị **hiển thị công khai** kèm nhãn ("đã bán 1,2k" là số làm tròn của sàn).
4. Vì lưu trữ là network mount "ghi sau thắng", dữ liệu phải **append-only**, mỗi lần chạy một file riêng.

---

## 2. Kiến trúc tổng thể

```
┌─────────────────────────── NGƯỜI DÙNG ───────────────────────────┐
│  Ngôn ngữ tự nhiên ở project chat · Duyệt quyết định · Cập nhật  │
│  trạng thái kinh doanh · Hoàn tất xác minh/đăng nhập khi cần      │
└───────────────┬──────────────────────────────────────▲───────────┘
                │ yêu cầu                               │ báo cáo, câu hỏi
┌───────────────▼──────────────────────────────────────┴───────────┐
│ LỚP ĐIỀU PHỐI  (Project coordinator → thread Claude Code)         │
│  aph-orchestrator: phân loại ý định → kế hoạch nghiên cứu →        │
│  chạy Agent theo thứ tự → cổng kiểm tra → cập nhật trạng thái     │
│  Scheduled tasks (daily / weekly / watchlist) khởi động thread    │
└───────────────┬──────────────────────────────────────────────────┘
                │ gọi skill theo kế hoạch
┌───────────────▼──────────────────────────────────────────────────┐
│ LỚP AGENT (7 Skills)                                              │
│ 01 Market Hunter · 02 Trend Hunter · 03 Demand Analyst ·          │
│ 04 Competitor Analyst · 05 Supplier Hunter · 06 Profit Analyst ·  │
│ 07 Opportunity Evaluator                                          │
│ + rules/ dùng chung: nguồn, xác minh, chấm điểm, an toàn          │
└───────┬───────────────────────┬──────────────────────┬───────────┘
        │ thu thập               │ tính toán             │ đọc/ghi
┌───────▼─────────┐   ┌──────────▼──────────┐   ┌───────▼───────────┐
│ LỚP THU THẬP    │   │ LỚP CÔNG CỤ         │   │ LỚP LƯU TRỮ       │
│ Claude Browser  │   │ tools/aph.py        │   │ data/ append-only │
│ (máy người dùng)│   │ dedup, score,       │   │ products, obs,    │
│ WebSearch       │   │ profit, confidence, │   │ runs, decisions,  │
│ Người dùng nhập │   │ index, report data  │   │ outcomes          │
└─────────────────┘   └─────────────────────┘   │ index.sqlite      │
                                                 └───────────────────┘
```

### 2.1 Vì sao chọn "Agent = Skill" thay vì 7 tiến trình riêng

- Claude Code chạy một phiên mỗi thread; Skill là cơ chế có sẵn để đóng gói quy trình chuyên biệt. Không cần tự xây runtime Agent.
- Giữ ngữ cảnh chung trong một phiên giúp Evaluator thấy toàn bộ bằng chứng, giảm chi phí so với 7 subagent.
- Bàn giao giữa Agent là **dữ liệu có cấu trúc trong kho**, không phải "trí nhớ hội thoại" → có thể chạy lại một bước, hoặc chạy từng Agent riêng theo yêu cầu.
- Khi cần song song (ví dụ: nghiên cứu nhà cung cấp cho 5 sản phẩm), orchestrator có thể dùng subagent nếu người dùng cho phép; hợp đồng dữ liệu giữ nguyên.

### 2.2 Ranh giới Claude Code ↔ Claude Cowork

| Việc | Ai làm | Ghi chú |
|---|---|---|
| Nhận yêu cầu, giao việc, nhắc lịch | Project coordinator | Đã có sẵn |
| Chạy quy trình nghiên cứu, ghi dữ liệu, tính toán | Thread Claude Code (cloud) | Đã kiểm chứng có Python/SQLite |
| Duyệt web có JS/đăng nhập | Claude Browser trên PC (gọi từ cloud qua remote-devices) | Đã kiểm chứng gọi được |
| Thao tác app desktop | Computer use trên PC | Chưa cần |
| Chạy theo lịch | Scheduled task của dự án (cloud, phiên mới mỗi lần) | **Đã kiểm chứng (2026-11-24):** có repo, WebSearch, ghi thư mục dự án; **không có** trình duyệt hay thư mục PC |
| Thu thập qua trình duyệt | Thread do người dùng mở, PC đã liên kết | Đã kiểm chứng. Đây là cách duy nhất lấy dữ liệu sàn TMĐT/MXH |
| ~~Dự phòng Cowork desktop~~ | Bỏ | Tác vụ trên PC không ghi được vào `/mnt/project-files` (ổ rclone trên cloud) |

### 2.3 Chu trình vận hành bán tự động (mỗi ngày)

1. **Lịch cloud (sáng, không cần người):** WebSearch tìm sản phẩm/tin mới ở thị trường nguồn; chấm điểm lại theo độ tươi dữ liệu; ghi Daily Report phần "từ kho"; lập **hàng đợi thu thập** `queue/YYYY-MM-DD.json` (sản phẩm Tier 1 cần làm mới, sản phẩm mới cần kiểm chứng ở VN, nguồn nào, ưu tiên). Kết thúc bằng thông báo cho người dùng.
2. **Phiên thu thập (người dùng mở thread, PC bật):** người dùng nói "chạy thu thập hôm nay"; orchestrator đọc hàng đợi, thu thập qua trình duyệt theo ngân sách trang; đến Shopee thì dừng, nhờ người dùng giải CAPTCHA trong khung trình duyệt rồi mới đọc; mục nào không làm được ghi `log-access` và giữ lại trong hàng đợi.
3. **Sau phiên thu thập:** chấm điểm, cổng trạng thái, hoàn thiện Daily Report.
4. Hàng đợi không được xử lý trong ngày được chuyển sang ngày sau; quá 3 ngày → báo trong Daily Report.

---

## 3. Cơ chế phối hợp Agent

### 3.1 Phân loại yêu cầu → kế hoạch

`aph-orchestrator` ánh xạ yêu cầu ngôn ngữ tự nhiên thành một **Research Plan**:

| Ví dụ yêu cầu | Ý định | Agent chạy | Độ sâu |
|---|---|---|---|
| "Tìm 10 sản phẩm nhà thông minh đang tăng trưởng tại VN" | discover | 01 → 02 → (03 nhanh) → 07 | quick |
| "Nghiên cứu sâu cảm biến rò rỉ nước" | deep_dive | 01(định danh) → 02 → 03 → 04 → 05 → 06 → 07 | deep |
| "Tìm 5 nhà cung cấp sản phẩm này trên 1688" | supplier | 05 (+06 cập nhật nếu có giá) | standard |
| "Phân tích khả năng kinh doanh trên Shopee" | viability | 04 → 06 → 07 | standard |
| "So sánh 5 sản phẩm tiềm năng nhất tuần này" | compare | 07 (đọc kho, chỉ bổ sung dữ liệu cũ quá hạn) | quick |
| "Theo dõi sản phẩm này 30 ngày" | watch | tạo watch rule; lịch chạy 02/04 định kỳ | — |
| "Đánh giá lại những sản phẩm tôi đã thử nghiệm" | learn | đọc outcomes → báo cáo hiệu chỉnh (đề xuất, không tự đổi trọng số) | — |

Độ sâu quy định ngân sách: số nguồn tối thiểu, số đối thủ/nhà cung cấp cần tìm, số trang được mở.

### 3.2 Hợp đồng giữa các bước

Mỗi Agent:
- **Input**: `product_id` (hoặc danh mục/từ khóa với Agent 01), `run_id`, độ sâu.
- **Output**: các `observation` và bản ghi chuyên biệt ghi vào kho qua `tools/aph.py`, cùng một `agent_summary` (tóm tắt + danh sách khoảng trống dữ liệu).
- **Phân công thu thập/phân tích**: Competitor Analyst là Agent duy nhất mở trang listing và đánh giá của sàn, ghi `listing` và trích đánh giá; Demand Analyst chỉ phân tích những gì đã có trong kho (cộng nguồn cộng đồng/TikTok mà Competitor không đọc), để không mở cùng một trang hai lần.
- **Completion criteria**: định nghĩa trong SKILL.md của Agent (ví dụ Competitor: ≥ N gian hàng quan sát được hoặc ghi rõ lý do không đạt).
- **Error handling**: mọi lỗi truy cập ghi `access_log` (url, thời điểm, loại lỗi: anti_bot / login_required / timeout / empty); không suy diễn thay thế.

### 3.3 Cổng kiểm tra và trạng thái nghiên cứu

Sau mỗi bước, orchestrator gọi `aph.py gate` để quyết định:

| Trạng thái nghiên cứu | Khi nào |
|---|---|
| `research_more` | Thiếu dữ liệu quan trọng nhưng tín hiệu ban đầu tốt |
| `watchlist` | Tín hiệu chưa đủ, cần theo dõi theo thời gian |
| `rejected` | Có bằng chứng loại trừ (cấm nhập, chứng nhận bắt buộc không đáp ứng, lợi nhuận âm ở kịch bản cơ sở...) — luôn kèm lý do |
| `qualified_opportunity` | Điểm ≥ 75 **và** Data Confidence ≥ ngưỡng (đề xuất 60) |

Sản phẩm không đạt một bước không bị xoá; nó chuyển trạng thái và vẫn được theo dõi.

### 3.4 Luồng 9 bước chuẩn

Discovery → Trend → Demand → Competition → Supplier → Profit → Evaluation → Memory (ghi liên tục, không phải bước cuối) → Reporting.
Bước 8 thực chất diễn ra ở **mọi** bước vì mỗi Agent ghi ngay vào kho.

### 3.5 Phát hiện 5 dạng cơ hội

Evaluator gắn nhãn cơ hội dựa trên quy tắc có thể kiểm tra, không chỉ cảm tính:

| Dạng | Tín hiệu kiểm tra |
|---|---|
| Emerging Product | Xuất hiện ở thị trường nguồn (CN/quốc tế), số người bán VN quan sát được thấp, có ≥ 2 mốc thời gian cho thấy tăng |
| Demand Gap | Agent 03 có cụm phàn nàn/nhu cầu lặp lại (≥ 3 nguồn độc lập) mà sản phẩm hiện có chưa giải quyết |
| Competition Gap | Có nhu cầu nhưng đối thủ ít, hoặc điểm đánh giá/nội dung/bảo hành của đối thủ yếu |
| Price Gap | Giá nhập có dữ liệu + chi phí đủ → biên lợi nhuận kịch bản cơ sở vượt ngưỡng ở mức giá cạnh tranh |
| Bundle Opportunity | Các sản phẩm trong kho có quan hệ `complements` (ví dụ cảm biến rò nước + van khóa + hub) và cùng phục vụ một vấn đề |

---

## 4. Mô hình dữ liệu

### 4.1 Nguyên tắc

- Mỗi dữ liệu quan trọng là một **Observation** có: giá trị, nguồn, thời điểm thu thập, trạng thái xác minh, độ tin cậy.
- Dữ liệu lịch sử **không bị ghi đè**; "giá trị hiện tại" = observation mới nhất còn hạn (`valid_until` hoặc theo độ tươi của từng loại chỉ số).
- Phân biệt rõ `observed` (thấy trực tiếp), `reported` (nguồn khác nói), `inferred` (AI suy luận), `estimated` (ước tính có khoảng), `user_input`.

### 4.2 Thực thể

| Thực thể | Trường chính |
|---|---|
| **Product** | `product_id`, `canonical_name`, `aliases[]` (vi/en/zh), `category_path`, `function_key` (khóa chuẩn hóa), `key_specs{}`, `research_status`, `business_status`, `relations[]` (complements/variant_of/substitute), `first_seen_at`, `created_by_run` |
| **Observation** | `obs_id`, `product_id`, `agent`, `metric` (price_vnd, seller_count, sold_label, review_score, search_interest, mention_count, moq, unit_cost_cny…), `value`, `unit`, `range_low/high`, `market` (VN/CN/global), `source_url`, `source_type` (marketplace/search/social/manufacturer/news/user), `collected_at`, `observed_at`, `verification`, `confidence` (0–1), `run_id`, `note` |
| **Listing** (đối thủ) | `listing_id`, `product_id`, `platform`, `shop_name`, `url`, `price`, `sold_label`, `rating`, `review_count`, `warranty`, `content_quality_note`, `strengths[]`, `weaknesses[]`, `collected_at` |
| **Supplier** | `supplier_id`, `name`, `platform`, `url`, `price_tiers[]`, `moq`, `oem_odm`, `lead_time`, `certifications[]`, `warranty`, `ship_to_vn`, `trust_signals[]`, `trust_level`, `quote_status` (`listed`/`need_quote`/`quoted`), `collected_at` |
| **DemandSignal** | `product_id`, `type` (product_demand/unmet_need/complaint/purchase_intent/price_sensitivity), `theme`, `evidence[]` (obs_id), `independent_sources` |
| **ProfitScenario** | `product_id`, `scenario` (conservative/base/optimistic), `inputs{}` mỗi input kèm obs_id hoặc assumption_id, `outputs{}` (gross, net, margin, breakeven_units, test_capital), `confidence` |
| **Assumption** | `assumption_id`, tên chi phí (phí sàn, phí thanh toán, ads %, đóng gói…), giá trị/khoảng, nguồn + ngày (ví dụ trang biểu phí chính thức của sàn), `valid_until` |
| **Evaluation** | `eval_id`, `product_id`, `scores{7 tiêu chí}`, `total`, `data_confidence`, `opportunity_types[]`, `cap_reason`, `weights_version`, `evaluated_at` |
| **Decision** | quyết định của người dùng + lý do + thời điểm |
| **Outcome** | kết quả thử nghiệm: số lượng bán, doanh thu, lợi nhuận thực, chi ads, tỷ lệ hoàn, phản hồi, kỳ báo cáo |
| **Run** | `run_id`, loại (manual/scheduled), yêu cầu gốc, kế hoạch, agent đã chạy, `access_log[]`, chi phí ước tính, kết quả |
| **WatchRule** | `product_id`, chỉ số theo dõi, tần suất, ngày kết thúc |

### 4.3 Chống trùng lặp

1. `function_key` chuẩn hóa = danh mục + chức năng chính + giao thức/thông số phân biệt (ví dụ `water-leak-sensor|zigbee`), viết thường, bỏ dấu.
2. Trước khi tạo Product mới: `aph.py match` tìm theo `function_key`, alias (vi/en/zh), và độ tương đồng tên.
3. Trùng khớp chắc chắn → gắn observation vào sản phẩm cũ. Nghi ngờ → tạo bản ghi `merge_candidate` để người dùng/Evaluator xác nhận; **không tự gộp** khi không chắc.
4. Biến thể (ví dụ WiFi vs Zigbee) là Product riêng có quan hệ `variant_of`.

### 4.4 Chấm điểm và Data Confidence

- Điểm tiềm năng 0–100 theo trọng số đặc tả (20/15/15/20/10/10/10), lưu trong `config/scoring_weights.json` có `version`.
- Mỗi tiêu chí có rubric định lượng trong `rules/scoring_rules.md` (ví dụ "Mức độ cạnh tranh" dựa trên số gian hàng quan sát, điểm đánh giá trung bình, chất lượng nội dung).
- **Data Confidence** 0–100 tính độc lập: trung bình có trọng số theo độ quan trọng của trường × hệ số xác minh (observed 1.0, reported 0.7, user_input 0.8, estimated 0.4, inferred 0.3) × hệ số độ tươi (giảm theo tuổi dữ liệu so với hạn của từng loại).
- **Quy tắc trần**: nếu thiếu/chưa xác minh dữ liệu trọng yếu (giá nhập, giá bán thị trường VN, tín hiệu nhu cầu), tổng điểm bị giới hạn ở 74 ("Cần nghiên cứu thêm") và ghi `cap_reason`.
- Học từ kết quả: `aph.py calibrate` so sánh điểm dự báo với Outcome; chỉ **đề xuất** điều chỉnh trọng số khi có ≥ 5 sản phẩm có kết quả (ngưỡng đề xuất), người dùng duyệt mới đổi `weights_version`.

### 4.5 Lợi nhuận

`Lợi nhuận = Doanh thu − Giá vốn (giá nhập + vận chuyển quốc tế + thuế/phí nhập) − Phí nền tảng − Quảng cáo − Vận chuyển người bán chịu − Đóng gói − Vận hành − Bảo hành/hoàn hàng dự kiến − Khác`.
- Ba kịch bản dùng cùng công thức, khác giả định.
- Thiếu giá nhập → kết quả `incomplete`, không xuất con số lợi nhuận; ghi "cần liên hệ báo giá".
- Giá bán tham khảo đối thủ chỉ là `reference_price`, không bao giờ được dùng như giá bán thực tế đã kiểm chứng.

---

## 5. Lưu trữ

### 5.1 Bố cục dữ liệu (thư mục dự án dùng chung)

```
/mnt/project-files/ai-product-hunter/data/
  products/<product_id>.json          # hồ sơ gốc (ít thay đổi, ghi có khóa)
  records/YYYY/MM/<run_id>.jsonl      # append-only, mỗi run một file; mọi loại bản ghi có trường "kind":
                                      # observation, listing, supplier, demand, trend, profit, evaluation,
                                      # decision, outcome, watch, access, note
  runs/<run_id>.json                  # nhật ký chạy
  assumptions/assumptions.json        # giả định chi phí có nguồn & hạn
  index.sqlite                        # dựng lại từ file bằng aph.py reindex
```

- Vì sao không ghi thẳng một SQLite trên network mount: nhiều thread có thể chạy cùng lúc, mount "ghi sau thắng", SQLite trên ổ mạng dễ hỏng khóa. File append-only theo run không bao giờ đè nhau; SQLite chỉ là chỉ mục để truy vấn nhanh, mất có thể dựng lại.
- Sửa hồ sơ Product dùng file khóa `products/.lock`. Khóa này **chỉ được bảo đảm trong một container** (đã thử: 20 tiến trình tranh, đúng 1 thắng); giữa hai thread khác nhau trên ổ rclone thì không bảo đảm.
- **Quy tắc một người ghi:** tại một thời điểm chỉ một run được tạo/sửa `products/` (run ghi `runs/<id>.json` với `finished_at=null` là đang giữ quyền). Run khác thấy có run đang mở dưới 2 giờ thì chỉ ghi bản ghi (`records/`, file riêng nên an toàn), không tạo sản phẩm mới. Sau mỗi run chạy `aph.py duplicates`.

### 5.2 Mã nguồn và skills

- Code, rules, schemas, config, nội dung SKILL.md: nên nằm trong **Git repo** (người dùng tạo trên GitHub rồi gắn vào dự án). Khi chưa có repo, tạm đặt trong `/mnt/project-files/ai-product-hunter/`.
- Skill được **đề xuất cho người dùng lưu** vào tài khoản qua thẻ duyệt; bản trong repo là nguồn chuẩn.
- Tùy chọn: xuất bản sao báo cáo sang `My Drive` trên PC để người dùng xem ngoài Claude (cần người dùng cấp quyền thư mục).

### 5.3 Project memory

Chỉ lưu sự thật lâu dài về dự án (mục tiêu, quyết định kiến trúc, sở thích người dùng). **Không** lưu dữ liệu nghiên cứu vào memory.

---

## 6. Lập lịch

| Job | Chạy ở đâu | Tần suất đề xuất | Việc làm |
|---|---|---|---|
| daily-prep | Lịch cloud | Mỗi ngày, sáng sớm giờ VN | WebSearch thị trường nguồn, chấm lại điểm, Daily Report phần kho, lập hàng đợi thu thập |
| daily-collect | Thread người dùng mở | Mỗi ngày làm việc, 10–20 phút | Thu thập theo hàng đợi qua trình duyệt PC, Shopee có người giải CAPTCHA |
| weekly-review | Lịch cloud | Thứ Hai | Evaluator xếp hạng lại, Weekly Report, đề xuất deep-dive |
| deep-dive | Thread người dùng mở | Khi người dùng duyệt | 7 Agent cho 1 sản phẩm |

**Phân tầng theo dõi** (giới hạn thật là thời gian trình duyệt và token, không phải lưu trữ):
- Tier 1: tối đa 20 sản phẩm, làm mới mỗi phiên thu thập.
- Tier 2: tối đa 200 sản phẩm, làm mới hằng tuần (chia đều các ngày).
- Tier 3: còn lại, chỉ lưu; làm mới khi có tín hiệu mới từ WebSearch hoặc người dùng yêu cầu.

**Xử lý lỗi lịch:**
- Mỗi run ghi `runs/<id>.json` lúc bắt đầu (`finished_at=null`) và khi kết thúc. Run sau thấy run trước chưa kết thúc quá 2 giờ → đánh dấu `abandoned` và báo.
- Lịch cloud đọc `list_triggers.last_run.status` của chính nó ở lần chạy sau; thất bại → chạy lại phần còn thiếu một lần; thất bại 2 lần liên tiếp → báo người dùng.
- Không chạy chồng: thấy một run cùng loại đang mở thì dừng.
- Lịch chỉ tạo khi người dùng đồng ý; tên, giờ chạy và nội dung được nêu rõ khi xin phép.

---

## 7. Báo cáo

| Loại | Định dạng | Nội dung |
|---|---|---|
| Daily | `reports/daily/YYYY-MM-DD.md` | Sản phẩm mới, xu hướng mới, đáng chú ý, cần nghiên cứu sâu, tín hiệu suy giảm, **danh sách nguồn không truy cập được** |
| Weekly | `reports/weekly/YYYY-Www.md` | Top 10 (điểm + Data Confidence), ngành tăng trưởng, cơ hội mới, thay đổi cạnh tranh, sản phẩm nên thử nghiệm |
| Deep Research | `reports/deep/<product_id>_YYYY-MM-DD.md` | 14 mục theo đặc tả, mỗi nhận định quan trọng có trích dẫn `[obs_id]` → URL + ngày |
| Dashboard | Artifact (Phase 4) | Opportunity Ranking, Watchlist, trạng thái kinh doanh |

Báo cáo được dựng từ dữ liệu trong kho bằng script, không viết số liệu tự do.

---

## 8. Kiểm soát chất lượng và an toàn

1. Không bịa số liệu/nguồn: mọi số trong báo cáo phải trỏ về observation có `source_url` (hoặc `user_input`). Observation `observed` bắt buộc có `evidence_excerpt`, tức đoạn văn bản trích nguyên văn từ trang chứa con số; công cụ kiểm tra con số có xuất hiện trong đoạn trích.
2. Dữ liệu cũ: báo cáo hiển thị ngày thu thập; quá hạn → gắn nhãn "dữ liệu cũ".
3. Không kết luận nhu cầu từ lượt xem; không kết luận xu hướng từ < 2 mốc thời gian hoặc 1 nguồn.
4. Không kết luận lợi nhuận khi chi phí chưa đủ (`incomplete`).
5. Nhà cung cấp: độ tin cậy dựa trên tín hiệu kiểm chứng (năm hoạt động, chứng nhận có số, đánh giá giao dịch), không dựa trên lời quảng cáo.
6. Không đặt hàng, thanh toán, nhắn tin nhà cung cấp thay người dùng.
7. Không vượt CAPTCHA/anti-bot, không nhập mật khẩu, không scraping hàng loạt; tôn trọng điều khoản từng nền tảng; giới hạn số trang mở mỗi run.
8. Lỗi truy cập luôn được ghi và báo cáo thay vì suy diễn.

---

## 9. Giới hạn kỹ thuật (đã xác định)

| # | Giới hạn | Ảnh hưởng | Cách xử lý |
|---|---|---|---|
| L1 | Mạng cloud là cấu hình môi trường (`limited`). Đã cho phép trends.google.com, www.alibaba.com, s.1688.com và thử: Trends 429, Alibaba/1688 trả trang chống bot | Không thu thập từ cloud | Dùng trình duyệt trên PC trong thread; WebSearch |
| L2 | Shopee bật trang xác minh chống bot | Không có dữ liệu Shopee tự động | Người dùng mở trang/hoàn tất xác minh thủ công khi cần; dữ liệu Seller Center của người dùng; nguồn gián tiếp. Ghi `anti_bot` |
| L3 | 1688/Taobao có thể cần đăng nhập, mã GBK | Supplier Hunter chưa chắc chạy được | Kiểm thử Phase 2; người dùng đăng nhập trong trình duyệt tích hợp nếu muốn; Alibaba.com làm nguồn thay thế |
| L4 | Trình duyệt chỉ chạy khi PC bật + app mở, và **chỉ trong thread có liên kết thiết bị** (phiên chạy theo lịch không có) | Không thu thập sàn theo lịch | Vận hành bán tự động (mục 2.3) |
| L5 | Không có lượt bán/GMV chính xác | Chỉ số bán hàng thô | Dùng nhãn hiển thị công khai, độ tin cậy thấp, nhiều mốc thời gian |
| L6 | WebSearch thiên về chỉ mục quốc tế | Yếu với thị trường VN | Dùng cho thị trường nguồn và nhà sản xuất; VN qua trình duyệt |
| L7 | Skill chỉ lưu được khi người dùng duyệt | Không tự cài skill | Đề xuất qua thẻ duyệt ở Phase 2 |
| L8 | Mount dùng chung "ghi sau thắng" | Rủi ro ghi đè | Append-only + khóa cho hồ sơ |
| L9 | Phiên cloud tạm thời | Mất file ngoài thư mục dự án | Mọi thứ ghi vào `/mnt/project-files` hoặc Git |
| L10 | Chi phí token/thời gian mỗi run | Không thể nghiên cứu sâu mọi sản phẩm hằng ngày | Ngân sách theo độ sâu; deep-dive cần duyệt |
| L11 | Cowork desktop scheduled tasks không điều khiển được từ cloud và không ghi được vào thư mục dự án | Không dùng làm dự phòng | Bỏ khỏi kiến trúc |
| L12 | Không làm mới được hàng nghìn sản phẩm định kỳ (mỗi trang 5–15 giây + token) | Chỉ theo dõi tích cực vài chục sản phẩm/ngày | Phân tầng Tier 1/2/3 |

---

## 10. Những gì có thể vận hành thực tế

| Năng lực | Mức khả thi | Ghi chú |
|---|---|---|
| Nhận yêu cầu ngôn ngữ tự nhiên, điều phối Agent | Cao | Cơ chế project/thread + skill có sẵn |
| Lưu trữ, chống trùng lặp, lịch sử, theo dõi dài hạn | Cao | Python/SQLite + thư mục dự án đã kiểm chứng |
| Chấm điểm, Data Confidence, mô phỏng lợi nhuận 3 kịch bản | Cao | Script tính toán xác định |
| Báo cáo có nguồn dẫn | Cao | Dựng từ kho |
| Chạy theo lịch | Trung bình | Đã kiểm chứng cho WebSearch/kho/báo cáo; **không** cho thu thập qua trình duyệt |
| Tìm sản phẩm ở thị trường quốc tế/nhà sản xuất | Trung bình–Cao | WebSearch + trình duyệt |
| Xu hướng (Google Trends, TikTok) | Trung bình–Cao | Đọc được bảng số Trends và TikTok CSI qua trình duyệt (trong thread) |
| Nhà cung cấp 1688/Alibaba | Trung bình | Đọc được qua trình duyệt, không cần đăng nhập (1688 cần mã GBK) |
| Đối thủ & đánh giá trên Shopee | Trung bình–Thấp | CAPTCHA kể cả khi đã đăng nhập; chỉ đọc được sau khi người dùng tự giải, trong phiên thu thập |
| Lượt bán chính xác | Không | Không có nguồn hợp lệ |
