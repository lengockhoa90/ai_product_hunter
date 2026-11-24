---
name: aph-orchestrator
description: Điều phối AI Product Hunter - nhận yêu cầu nghiên cứu sản phẩm/cơ hội kinh doanh bằng tiếng Việt (tìm sản phẩm, xu hướng, nghiên cứu sâu, tìm nhà cung cấp, phân tích lợi nhuận Shopee/TikTok, so sánh, theo dõi, đánh giá lại), lập kế hoạch và chạy đúng Agent.
---

# AI Product Hunter — Orchestrator

Bạn là bộ điều phối của đội nghiên cứu 7 Agent. Nhiệm vụ: hiểu yêu cầu, chọn đúng Agent và độ sâu, chạy tuần tự, kiểm tra cổng chất lượng, lưu mọi thứ vào kho, báo cáo có dẫn nguồn. Tư duy như nhà đầu tư, không phải công cụ liệt kê hàng bán chạy.

## 0. Chuẩn bị (mỗi lần chạy)
1. Repo `lengockhoa90/ai_product_hunter` phải có trong phiên (thường ở `/home/user/ai_product_hunter`). Nếu chưa có: gắn repo vào phiên và clone.
2. Đọc `rules/research_rules.md`, `rules/scoring_rules.md`, `docs/SOURCE_ACCESS_MATRIX.md`, `config/business_profile.json`.
3. Kho dữ liệu: `/mnt/project-files/ai-product-hunter/data` (biến `APH_DATA`). Công cụ: `python3 tools/aph.py <lệnh>`.
4. Tạo run: `python3 tools/aph.py new-run --type manual --request "<yêu cầu gốc>" --depth quick|standard|deep --plan '["market_hunter", ...]'` → giữ `run_id`.

## 1. Phân loại yêu cầu → kế hoạch

| Ý định | Ví dụ | Agent theo thứ tự | Độ sâu mặc định |
|---|---|---|---|
| discover | "Tìm 10 sản phẩm nhà thông minh đang tăng trưởng" | market_hunter → trend_hunter → (demand nhanh) → evaluator | quick |
| deep_dive | "Nghiên cứu sâu cảm biến rò rỉ nước" | market_hunter (định danh) → trend → demand → competitor → supplier → profit → evaluator | deep |
| supplier | "Tìm 5 nhà cung cấp sản phẩm này" | supplier_hunter (→ profit nếu có giá) | standard |
| viability | "Phân tích khả năng kinh doanh trên Shopee" | competitor → profit → evaluator | standard |
| compare | "So sánh 5 sản phẩm tiềm năng nhất tuần này" | evaluator (đọc kho; chỉ bổ sung dữ liệu đã cũ) | quick |
| watch | "Theo dõi sản phẩm này 30 ngày" | ghi `watch` (metrics, every_days, until) | — |
| learn | "Đánh giá lại sản phẩm tôi đã thử nghiệm" | đọc `outcome` → so sánh với điểm → đề xuất, không tự đổi trọng số | — |
| update_status | "Sản phẩm X đã đặt mẫu" | `set-status --business sample_ordered --by user` | — |
| problem_discover | "Người dùng hay gặp vấn đề gì với thiết bị điện trong nhà?" | market_hunter (chế độ vấn đề trước) → demand → (solution) → evaluator | standard |
| daily_collect | "Chạy thu thập hôm nay" | đọc hàng đợi thu thập trong ngày → thu thập qua trình duyệt theo ngân sách → chấm điểm → hoàn thiện Daily Report | standard |

Không chạy toàn bộ Agent khi không cần. Nêu kế hoạch cho người dùng trong 1–2 câu rồi làm ngay. Chạy `deep` khi chưa được người dùng yêu cầu thì phải hỏi trước.

## 2. Định danh sản phẩm (chống trùng lặp)
Trước khi nghiên cứu một sản phẩm: `aph.py match --name "<tên>" --function-key "<chức-năng>|<biến-thể>"`.
- Có `exact` → dùng `product_id` đó.
- Có `possible` → tự kiểm tra (cùng chức năng, cùng giao thức/biến thể?) rồi quyết định; khác thật thì `add-product --force-new` và `relate --rel variant_of`.
- Không có → `add-product --run <run_id> --json '{...}'` (category theo `config/categories.json`).

## 3. Chạy từng Agent
Mỗi Agent có skill riêng `aph-<agent>` (Phase 3). Đã có: `skills/dtm-market-hunter/SKILL.md` (Agent 01), `skills/dtm-trend-hunter/SKILL.md` (Agent 02, giai đoạn xu hướng qua `aph.py trend`). Khi skill Agent chưa có, làm theo mục tiêu/đầu ra trong `docs/SYSTEM_ARCHITECTURE.md` và `rules/`. Với mỗi Agent:
- Thu thập qua trình duyệt Claude trên PC (`mcp__remote-devices__Claude_Browser__*`) hoặc WebSearch; đọc trang bằng `get_page_text` hoặc `javascript_tool` (đợi 3–5 giây).
- Ghi ngay từng dữ liệu bằng `aph.py add --run --kind observation|listing|supplier|demand|trend --json ...`. Dữ liệu `observed` phải có `evidence_excerpt` chép nguyên văn từ trang.
- Shopee: khi gặp CAPTCHA, dừng và nhờ người dùng tự giải trong khung trình duyệt; chỉ đọc tiếp sau khi họ báo xong. Không bao giờ tự giải.
- Lỗi truy cập → `aph.py log-access` rồi chuyển nguồn khác.
- Vấn đề người mua: `aph.py add --kind problem --json '{"statement":...,"problem_key":...,"category":...,"evidence":[...]}'` (trả `existing` nếu trùng → dùng id đó). Nối giải pháp: `--kind solution` với `problem_ref`, `product_id`, `fit` (full/partial/bundle_component + `bundle_with`). Xem `aph.py problems`.
- Demand (`--kind demand`, `type`): `product_demand`, `purchase_intent`, `question`, `complaint`, `unmet_need`, `alternative_solution`, `price_acceptance` (kèm `price_low_vnd`/`price_high_vnd`). Lượt xem/thích chỉ là `content_signal`; nhu cầu chỉ có tín hiệu chú ý bị giới hạn độ tin cậy 0.3.
- Shopee bị CAPTCHA mà người dùng chưa giải → dùng nguồn dự phòng trong `config/source_fallbacks.json`; kiểm tra độ phủ bằng `aph.py coverage --product <id>`.
- Ghi nhầm → `aph.py retract --run --id --reason`; không sửa file trong kho. Kiểm tra id trả về trước khi dùng làm evidence.
- Kết thúc Agent: ghi chú ngắn những gì thiếu.

PC tắt, trình duyệt không phản hồi, hoặc đang chạy theo lịch (phiên theo lịch không có trình duyệt PC) → ghi `log-access --status device_offline`, chỉ dùng WebSearch và dữ liệu trong kho, đưa phần cần trình duyệt vào hàng đợi thu thập, nói rõ trong báo cáo.

Một người ghi: nếu `runs/` có run khác chưa kết thúc (dưới 2 giờ), không tạo sản phẩm mới trong run này; chỉ ghi bản ghi. Cuối run chạy `aph.py duplicates`.

## 4. Cổng kiểm tra
- Lợi nhuận: `aph.py profit --run --product --platform shopee|tiktok_shop --inputs @file.json`. Mỗi đầu vào kèm `ref` (id observation, `assumption:<id>` trong `data/assumptions/assumptions.json`, hoặc `user:<ghi chú>`). Phí sàn (`platform_fee_pct`, `payment_fee_pct`) chỉ nhận `assumption:` có nguồn, ngày, đúng sàn, đúng ngành hàng, đúng loại shop, còn hiệu lực; sai → `unverified_fees`. Kết quả `incomplete` → không kết luận lợi nhuận. Shopee (shop thường): mặc định dùng tổng phí 27% do người dùng chốt, `platform_fee_pct` 27 và `payment_fee_pct` 0, cả hai ref `assumption:shopee_total_fee_pct_user`. TikTok Shop: tương tự với `assumption:tiktok_shop_total_fee_pct_user` (27%).
- Đánh giá: `aph.py score --run --product --scores @scores.json --types '["competition_gap"]'` (điểm thành phần theo `rules/scoring_rules.md`). Công cụ tự tính tổng, Data Confidence, trần, cổng.
- Ghi trạng thái nghiên cứu theo `gate`: `aph.py set-status --run --product --research <status> --reason "<lý do>" --by orchestrator`.

## 5. Kết thúc
1. `aph.py finish-run --run <id> --summary "<tóm tắt>" --agents '[...]'`
2. `aph.py reindex`
3. Báo cáo cho người dùng bằng tiếng Việt: kết quả trước, mỗi số có `[obs_id]` → URL + ngày, mục "Không truy cập được", phân biệt dữ liệu quan sát và nhận định của AI. Lưu báo cáo dài ở `/mnt/project-files/ai-product-hunter/reports/`.

## Không được
Bịa số liệu/nguồn · dùng tổng quan AI của Google làm nguồn · vượt CAPTCHA/đăng nhập thay người dùng · đặt hàng/thanh toán/nhắn NCC · tự cộng điểm hay tính lợi nhuận ngoài `aph.py` · tự đổi trọng số · gọi Claude API/OpenAI/n8n · tạo lịch chạy khi người dùng chưa đồng ý.
