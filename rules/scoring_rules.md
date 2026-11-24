# Quy tắc chấm điểm (Opportunity Evaluator)

> **Từ 2026-11-24 Agent 07 dùng chấm điểm v2** (`aph.py evaluate`, `config/scoring_weights_v2.json`, `docs/AGENT_07_AUDIT_DESIGN.md` mục 7): 5 nhóm 0–100 theo từng model, nhóm thiếu dữ liệu là null (không phải 0), Risk Gates. Phần dưới là chấm điểm v1 (`aph.py score`), giữ để đọc lịch sử.

Điểm tiềm năng và Data Confidence là hai chỉ số độc lập. Cả hai do `aph.py score` tính; Evaluator chỉ chấm điểm thành phần 0–10 cho từng tiêu chí, kèm lý do và id bằng chứng.

## Điểm thành phần 0–10

| Tiêu chí (trọng số) | 0–3 | 4–6 | 7–8 | 9–10 |
|---|---|---|---|---|
| demand (20) | Không có tín hiệu ngoài lượt xem | Có tìm kiếm hoặc đã bán nhưng thấp/1 nguồn | Tìm kiếm + đã bán ở ≥ 2 nguồn | Nhu cầu rõ + vấn đề lặp lại của người mua |
| growth (15) | Giảm hoặc 1 mốc | Đi ngang | Tăng ở ≥ 2 mốc, 1 nguồn | Tăng ở ≥ 2 mốc, ≥ 2 nguồn độc lập |
| competition (15) — điểm cao = ít cạnh tranh | Rất nhiều người bán mạnh, giá đã đáy | Nhiều người bán, có khác biệt nhỏ | Ít người bán hoặc đối thủ yếu (đánh giá/nội dung/bảo hành) | Gần như chưa có người bán VN, nhu cầu đã có |
| profit (20) | Lợi nhuận ròng âm ở kịch bản cơ sở | Biên ròng < 10% | 10–20% | > 20% và kịch bản thận trọng vẫn dương |
| sourcing (10) | Chưa tìm được NCC | Chỉ có NCC nước ngoài | ≥ 1 NCC trong nước có giá | ≥ 2 NCC trong nước có giá, MOQ hợp vốn |
| differentiation (10) | Hàng đồng nhất, không có điểm yếu đối thủ để khai thác | Khác biệt nhỏ (đóng gói, nội dung) | Có điểm đau rõ của khách mà đối thủ chưa giải quyết | Có thể làm bundle/giải pháp hoặc bản phù hợp VN |
| fit (10) | Lô thử vượt 10 triệu hoặc cần giấy phép không có | Vừa vốn nhưng khó bán trên Shopee/TikTok | Vừa vốn, bán được trên kênh chính | Vừa vốn, hợp danh mục ưu tiên, dễ làm nội dung TikTok |

Không có bằng chứng trong kho cho một tiêu chí → để `"score": null` (công cụ tính 0 điểm và ghi cờ). Công cụ cũng tự đặt 0 nếu có điểm mà không có bằng chứng.

## Data Confidence (công cụ tự tính)
- Mỗi bằng chứng: hệ số xác minh × `confidence` × độ tươi (bán rã theo loại chỉ số).
- Mỗi tiêu chí: trung bình 3 bằng chứng tốt nhất × độ phủ nguồn (số tên miền độc lập / số tối thiểu) × (growth) số mốc thời gian / 2.
- Tổng: trung bình có trọng số theo trọng số tiêu chí, thang 0–100.

## Trần điểm
Nhu cầu chỉ dựa trên Google Trends, lượt xem hoặc lượt thích (tín hiệu chú ý) không được quá mức 1 ở cột demand; công cụ giới hạn độ tin cậy của nhu cầu ở 0.3 trong trường hợp này.

Nếu tiêu chí trọng yếu (demand, competition, profit) có độ tin cậy < 0.5 thì tổng điểm bị giới hạn ở 74 và ghi `cap_reason`.

## Cổng trạng thái (`aph.py gate`)
- `qualified_opportunity`: điểm ≥ 75, Data Confidence ≥ 60, không bị trần.
- `research_more`: bị trần, hoặc điểm ≥ 60.
- `watchlist`: 40–59.
- `rejected`: < 40. Chỉ ghi `rejected` khi có lý do cụ thể (`set-status --reason`).

## 5 dạng cơ hội (`--types`)
`emerging_product`, `demand_gap`, `competition_gap`, `price_gap`, `bundle_opportunity`. Mỗi nhãn phải chỉ ra bằng chứng theo bảng ở SYSTEM_ARCHITECTURE.md mục 3.5.

## Học từ kết quả
Không đổi `config/scoring_weights.json` dựa trên một sản phẩm. Khi có ≥ 5 sản phẩm có `outcome`, so sánh điểm dự báo với kết quả và **đề xuất** phiên bản trọng số mới cho người dùng duyệt.
