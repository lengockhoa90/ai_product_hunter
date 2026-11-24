# AI Product Hunter — hướng dẫn cho Claude

Hệ thống nghiên cứu cơ hội kinh doanh cho thị trường Việt Nam (Shopee, TikTok Shop). Người dùng: Khoa Le. Trả lời bằng tiếng Việt.

## Bắt đầu
- Yêu cầu nghiên cứu → làm theo `skills/aph-orchestrator/SKILL.md`.
- Kiến trúc: `docs/SYSTEM_ARCHITECTURE.md`. Lộ trình và trạng thái: `docs/IMPLEMENTATION_ROADMAP.md`. Khả năng truy cập nguồn: `docs/SOURCE_ACCESS_MATRIX.md`.
- Quy tắc bắt buộc: `rules/research_rules.md`, `rules/scoring_rules.md`.

## Dữ liệu
- Kho: `/mnt/project-files/ai-product-hunter/data` (không commit vào repo). Ghi/đọc **chỉ qua** `python3 tools/aph.py`.
- Báo cáo: `/mnt/project-files/ai-product-hunter/reports/{daily,weekly,deep}/`.
- Config trong repo: `config/` (trọng số có version, hồ sơ kinh doanh, ngân sách độ sâu). Giả định chi phí có nguồn: `data/assumptions/assumptions.json`.

## Kiểm thử
`python3 -m unittest discover -s tests -v` (chỉ dùng thư mục tạm, không ghi vào kho thật). Chạy sau mọi thay đổi ở `tools/`, `config/`, `schemas/`.

## Ràng buộc
- Chỉ hệ sinh thái Claude. Không Claude API, OpenAI API, n8n, dịch vụ AI trung gian.
- Vận hành bán tự động: lịch cloud không có trình duyệt PC (đã kiểm chứng); truy cập trực tiếp từ cloud bị chặn chống bot. Dữ liệu sàn chỉ thu thập trong thread có PC liên kết; lịch chỉ dùng WebSearch, kho và báo cáo.
- Không vượt CAPTCHA, không nhập mật khẩu, không đặt hàng/thanh toán, không nhắn tin nhà cung cấp.
- Không tạo dữ liệu giả, kể cả để minh họa. Không đánh dấu hoàn thành khi chưa kiểm thử thật.
- Tạo scheduled task, đẩy code lên `main`, hay việc ngoài dự án: hỏi người dùng trước.
