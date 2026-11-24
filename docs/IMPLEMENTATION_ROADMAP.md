# AI PRODUCT HUNTER — IMPLEMENTATION ROADMAP

> Phiên bản: 0.1 · Ngày: 2026-11-24 · Đi kèm: `SYSTEM_ARCHITECTURE.md`
> Quy ước trạng thái: ✅ xong và đã kiểm thử · 🟡 đang làm · ⬜ chưa làm

---

## Tổng quan giai đoạn

| Phase | Mục tiêu | Trạng thái |
|---|---|---|
| 1 | Kiến trúc + kiểm tra môi trường | ✅ |
| 2 | Core system: kho dữ liệu, công cụ, orchestrator, rules, kiểm thử truy cập nguồn | 🟡 (xong phần code + kiểm thử; chờ duyệt PR và lưu skill) |
| 3 | 7 Agent (lần lượt, mỗi Agent có kiểm thử thật) | ⬜ |
| 4 | Báo cáo + dashboard | ⬜ |
| 5 | Tự động hóa theo lịch | ⬜ |
| 6 | Kiểm thử tổng thể + báo cáo kiểm thử | ⬜ |

Nguyên tắc: mỗi bước chỉ đánh dấu ✅ khi có kiểm thử thật trên dữ liệu thật; không tạo dữ liệu giả để minh họa.

---

## Quyết định cần người dùng trước/đầu Phase 2

| # | Câu hỏi | Đề xuất |
|---|---|---|
| D1 | Tạo Git repo (GitHub) cho code/skills/rules? | Có. Người dùng tạo repo trống (ví dụ `ai-product-hunter`), Claude gắn vào dự án |
| D2 | Shopee: chấp nhận mô hình bán tự động (người dùng tự mở/xác minh trang khi cần) hay bỏ Shopee khỏi thu thập tự động? | Bán tự động + dữ liệu Seller Center của người dùng nếu có |
| D3 | Có đăng nhập 1688/Taobao trong trình duyệt tích hợp của Claude không? (người dùng tự đăng nhập, Claude không nhập mật khẩu) | Thử Alibaba.com/1688 không đăng nhập trước, quyết định sau kiểm thử |
| D4 | Thông tin kinh doanh: vốn thử nghiệm tối đa, kênh bán chính, khả năng nhập khẩu (tiểu ngạch/chính ngạch), có shop sẵn chưa | Người dùng cung cấp để Agent 06/07 chấm "Mức độ phù hợp kinh doanh" |
| D5 | Có mirror báo cáo sang `My Drive` trên PC không? | Tùy chọn, làm ở Phase 4 |
| D6 | Quan hệ với skill `trend-hunter-dtm` hiện có | Giữ nguyên skill đó (xu hướng nội dung video). Agent 02 mới tập trung xu hướng **sản phẩm**, dùng lại kỹ thuật đọc Google Trends/CSI |

### Câu trả lời của người dùng (2026-11-24)

| # | Trả lời | Ảnh hưởng thiết kế |
|---|---|---|
| D1 | Đã tạo GitHub repo trống | Cần link repo và thêm repo vào dự án trong Project settings trước khi commit code |
| D2 | Đã đăng nhập một tài khoản Shopee trong trình duyệt của Claude | Phase 2.1 kiểm thử lại Shopee khi đã đăng nhập: nếu vẫn bị trang xác minh thì người dùng tự hoàn tất; chỉ đọc trang, số trang mỗi lần chạy có giới hạn, không thao tác trên tài khoản |
| D3 | Tìm nguồn hàng **trong nước** | Agent 05 ưu tiên nhà phân phối, nhà sản xuất và đại lý sỉ tại Việt Nam; 1688/Alibaba/Taobao chỉ dùng để tham khảo giá gốc và phát hiện sản phẩm, không phải nguồn nhập chính |
| D4 | Vốn thử nghiệm 10.000.000 đ; kênh chính Shopee và TikTok Shop | `config/business_profile.json`: giới hạn vốn thử nghiệm 10 triệu, nên sản phẩm có MOQ hoặc lô thử nghiệm vượt mức này bị trừ điểm "Mức độ phù hợp kinh doanh" và được gắn cảnh báo |

---

## PHASE 2 — CORE SYSTEM

**Mục tiêu:** nền móng chạy được, chưa nghiên cứu phức tạp.

| # | Hạng mục | Sản phẩm | Tiêu chí hoàn thành |
|---|---|---|---|
| 2.1 | Kiểm thử truy cập nguồn | `docs/SOURCE_ACCESS_MATRIX.md` | Mỗi nguồn (Shopee, TikTok, TikTok CSI, Google Trends, Google, 1688, Alibaba, Taobao, Lazada, Amazon, AliExpress, website nhà sản xuất VN/CN) có kết quả thật: đọc được / cần đăng nhập / anti-bot / lỗi, kèm ngày kiểm tra |
| 2.2 | Schemas | `schemas/entities.json` (một file, công cụ tự kiểm tra) cho Product, Observation, Listing, Supplier, DemandSignal, ProfitScenario, Assumption, Evaluation, Decision, Outcome, Run, WatchRule | Validate được bằng script |
| 2.3 | Công cụ `tools/aph.py` | Lệnh: `init`, `new-run`, `match`, `add-product`, `add-obs`, `add-listing`, `add-supplier`, `gate`, `score`, `profit`, `confidence`, `reindex`, `query`, `log-access` | Unit test (`tests/`) với input do test tự sinh trong thư mục tạm, không ghi vào kho thật |
| 2.4 | Chống trùng lặp | `match` theo function_key + alias + độ tương đồng; `merge_candidate` | Test các ca: trùng tên khác ngôn ngữ, biến thể WiFi/Zigbee, tên gần giống nhưng khác sản phẩm |
| 2.5 | Rules dùng chung | `rules/research_rules.md` (bằng chứng, xác minh, truy cập nguồn, an toàn), `rules/scoring_rules.md` | Đọc được, được skill tham chiếu |
| 2.6 | Config | `config/scoring_weights.json` (v1 theo đặc tả), `config/categories.json` (9 nhóm ưu tiên), `config/depth_budgets.json` | `aph.py score` dùng được |
| 2.7 | Giả định chi phí | `data/assumptions/assumptions.json` | **Chỉ** điền từ nguồn chính thức (biểu phí Shopee/TikTok Shop, đơn vị vận chuyển) có URL + ngày; mục chưa có nguồn để trống và đánh dấu `missing` |
| 2.8 | Orchestrator | `skills/aph-orchestrator/SKILL.md` | Với 7 câu lệnh mẫu trong đặc tả, sinh ra Research Plan đúng (kiểm thử khô, chưa chạy Agent) |
| 2.9 | `CLAUDE.md` dự án | Hướng dẫn cho mọi thread: đường dẫn kho, quy tắc, lệnh công cụ | — |
| 2.10 | Đề xuất lưu skill | Thẻ duyệt `aph-orchestrator` | Người dùng lưu |

**Kiểm thử Phase 2:** chạy `tests/` + một run thật tối thiểu: tạo 1 sản phẩm thật từ một nguồn thật, thêm 2 observation thật, chạy lại để kiểm tra không tạo trùng.

---

## PHASE 3 — RESEARCH MODULES (7 Agent)

Mỗi Agent là `skills/aph-<agent>/SKILL.md` gồm: Mục tiêu · Input · Output · Quy trình · Công cụ · Tiêu chí hoàn thành · Xử lý lỗi. Thứ tự triển khai theo phụ thuộc dữ liệu và độ khả thi:

| Thứ tự | Agent | Lý do thứ tự | Kiểm thử thật |
|---|---|---|---|
| 3.1 | 01 Market Hunter | Tạo đầu vào cho mọi Agent | Tìm ≥ 10 sản phẩm thật trong nhóm "nhà thông minh", mỗi sản phẩm có nguồn + ngày; phân biệt "mới trên thị trường" và "mới với người nghiên cứu" |
| 3.2 | 06 Profit Analyst | Thuần tính toán, khả thi cao | Đối chiếu kết quả `aph.py profit` với bảng tính tay độc lập trên 3 bộ số; thiếu giá nhập → `incomplete` |
| 3.3 | 07 Opportunity Evaluator | Cần sớm để thấy toàn luồng | Điểm + Data Confidence + trần 74 hoạt động đúng; nhãn 5 dạng cơ hội |
| 3.4 | 02 Trend Hunter | Phụ thuộc Google Trends/TikTok qua trình duyệt | ≥ 2 mốc thời gian cho mỗi kết luận; phân loại Emerging→Declining |
| 3.5 | 05 Supplier Hunter | Phụ thuộc kết quả kiểm thử 1688/Alibaba | ≥ 3 nhà cung cấp thật cho 1 sản phẩm, hoặc báo cáo rõ nguồn nào bị chặn |
| 3.6 | 04 Competitor Analyst | Khó nhất do Shopee anti-bot | Theo mô hình đã chọn ở D2; ghi đầy đủ `access_log` |
| 3.7 | 03 Demand Analyst | Cần đánh giá/bình luận, phụ thuộc 04 và nguồn cộng đồng | Cụm nhu cầu/phàn nàn có ≥ 3 nguồn độc lập |
| 3.8 | Tích hợp | Deep-dive 1 sản phẩm thật end-to-end (ví dụ cảm biến rò rỉ nước) | Mọi nhận định có trích dẫn; không số liệu không nguồn |

Mỗi skill được đề xuất cho người dùng lưu sau khi kiểm thử đạt.

---

## PHASE 4 — REPORTING

| # | Hạng mục | Tiêu chí |
|---|---|---|
| 4.1 | Generator Daily Report | Dựng từ kho; có mục "nguồn không truy cập được" |
| 4.2 | Generator Weekly Report | Top 10 có điểm + Data Confidence + thay đổi so với tuần trước |
| 4.3 | Template Deep Research 14 mục | Trích dẫn `[obs_id]` → URL + ngày |
| 4.4 | Product Watchlist + Opportunity Ranking | Artifact dashboard đọc dữ liệu đã xuất |
| 4.5 | Nhập trạng thái kinh doanh & Outcome | Người dùng cập nhật bằng câu lệnh tự nhiên ("sản phẩm X đã đặt mẫu") |

---

## PHASE 5 — AUTOMATION

Chỉ bắt đầu khi người dùng đồng ý tạo scheduled task.

| # | Job | Tiêu chí |
|---|---|---|
| 5.1 | Kiểm thử: scheduled run có gọi được trình duyệt trên PC không | Kết quả thật, ghi vào SOURCE_ACCESS_MATRIX |
| 5.2 | daily-discovery | Chạy 3 ngày liên tiếp không lỗi chặn, có Daily Report |
| 5.3 | watchlist-refresh | Thêm observation mới, không tạo bản ghi trùng |
| 5.4 | weekly-review | Weekly Report đúng hạn |
| 5.5 | deep-dive-on-trigger | Đề xuất deep-dive, chỉ chạy khi người dùng duyệt |
| 5.6 | Dự phòng Cowork desktop | Hướng dẫn tạo task trên desktop nếu cloud không dùng được trình duyệt |

---

## PHASE 6 — TESTING

Báo cáo `docs/TEST_REPORT.md` với kết quả thật cho từng mục:
tìm sản phẩm · xác minh dữ liệu · phân tích đối thủ · tìm nhà cung cấp · độ chính xác lợi nhuận · phối hợp Agent · lưu/cập nhật · phát hiện trùng lặp · tạo báo cáo · vận hành theo lịch.
Đối chiếu 12 tiêu chí nghiệm thu (mục XIV của đặc tả), mục nào chưa đạt ghi rõ lý do.

---

## Cấu trúc thư mục đề xuất

Code/skills/rules nằm trong Git repo (khi có) — bản làm việc được đồng bộ vào thư mục dự án. Dữ liệu và báo cáo luôn nằm trong thư mục dự án dùng chung.

```
ai-product-hunter/                    # Git repo (D1) + bản sao trong /mnt/project-files/ai-product-hunter/
├── CLAUDE.md                         # hướng dẫn cho mọi phiên Claude
├── README.md
├── docs/
│   ├── SYSTEM_ARCHITECTURE.md        # ✅ Phase 1
│   ├── IMPLEMENTATION_ROADMAP.md     # ✅ Phase 1
│   ├── SOURCE_ACCESS_MATRIX.md       # Phase 2
│   └── TEST_REPORT.md                # Phase 6
├── skills/
│   ├── aph-orchestrator/SKILL.md
│   ├── aph-market-hunter/SKILL.md
│   ├── aph-trend-hunter/SKILL.md     # khác skill trend-hunter-dtm (nội dung video)
│   ├── aph-demand-analyst/SKILL.md
│   ├── aph-competitor-analyst/SKILL.md
│   ├── aph-supplier-hunter/SKILL.md
│   ├── aph-profit-analyst/SKILL.md
│   └── aph-opportunity-evaluator/SKILL.md
├── rules/
│   ├── research_rules.md
│   └── scoring_rules.md
├── schemas/entities.json             # định nghĩa trường cho mọi bản ghi
├── config/
│   ├── scoring_weights.json
│   ├── categories.json
│   ├── confidence.json
│   ├── business_profile.json
│   └── depth_budgets.json
├── tools/
│   ├── aph.py                        # CLI duy nhất: dedup, obs, score, profit, confidence, gate, reindex
│   └── report.py                     # dựng Daily/Weekly/Deep từ kho
├── templates/
│   ├── daily_report.md
│   ├── weekly_report.md
│   └── deep_research_report.md
└── tests/

/mnt/project-files/ai-product-hunter/  # chỉ ở thư mục dự án (không commit)
├── data/
│   ├── products/
│   ├── records/YYYY/MM/<run_id>.jsonl
│   ├── runs/
│   ├── assumptions/
│   └── index.sqlite                  # dựng lại được
└── reports/
    ├── daily/
    ├── weekly/
    └── deep/
```

`templates/`, `tools/report.py` và các skill Agent được tạo ở Phase 3–4 khi có nội dung thật.

---

## Thứ tự làm tiếp theo (đề xuất)

1. Người dùng trả lời D1–D4.
2. Phase 2.1: kiểm thử truy cập từng nguồn → biết chính xác Agent nào khả thi.
3. Phase 2.2–2.7: schemas, `aph.py`, rules, config, tests.
4. Phase 2.8: orchestrator + đề xuất lưu skill.
5. Phase 3 theo thứ tự 01 → 06 → 07 → 02 → 05 → 04 → 03, rồi deep-dive end-to-end một sản phẩm thật.

---

## Kết quả Phase 2 (2026-11-24)

| # | Hạng mục | Trạng thái | Bằng chứng |
|---|---|---|---|
| 2.1 | Kiểm thử truy cập nguồn | ✅ | `docs/SOURCE_ACCESS_MATRIX.md`. Shopee bị CAPTCHA kể cả khi đã đăng nhập; TikTok, TikTok CSI, Google Trends (đọc được bảng số), Google VN, Lazada, Alibaba, 1688 (GBK) đều đọc được |
| 2.2 | Schemas | ✅ | `schemas/entities.json` |
| 2.3–2.4 | `tools/aph.py` + chống trùng lặp | ✅ | 24 unit test đạt (`tests/test_aph.py`), gồm tính lợi nhuận đối chiếu số tính tay |
| 2.5 | Rules | ✅ | `rules/research_rules.md`, `rules/scoring_rules.md` |
| 2.6 | Config | ✅ | trọng số v1, độ tin cậy, danh mục, ngân sách độ sâu, hồ sơ kinh doanh (10 triệu, Shopee + TikTok, nguồn trong nước) |
| 2.7 | Giả định chi phí | 🟡 | Có: Phí Xử Lý Giao Dịch Shopee 6% và Voucher Xtra 5,5% (tối đa 50.000 đ) từ banhang.shopee.vn/edu/article/21087 (bài cho Shopee Mall, ngày 14/05/2026). Thiếu: phí cố định theo ngành hàng, phí TikTok Shop → lấy ở deep-dive đầu tiên |
| 2.8 | Orchestrator | ✅ (chờ người dùng lưu skill) | `skills/aph-orchestrator/SKILL.md` |
| 2.9 | `CLAUDE.md` | ✅ | |
| Run thật | ✅ | `run_20261124…`: 1 sản phẩm (cảm biến rò rỉ nước Zigbee), 2 observation, 2 NCC tham khảo, 1 lỗi Shopee CAPTCHA; thêm lại bằng tên khác (en/zh) → nhận ra sản phẩm cũ, không tạo trùng. Data Confidence = 37,9 (thiếu lợi nhuận, khác biệt, phù hợp) |

Phát hiện từ run thật: Google Trends cho "cảm biến rò rỉ nước" tại VN gần như bằng 0 suốt 12 tháng (so với "công tắc thông minh" trung bình 56). Đây là dữ liệu quan sát, chưa phải kết luận về sản phẩm.
