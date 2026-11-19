# Universal Checkout Backend — bản MVP

Backend một merchant, một chain và một Router, khớp PaymentRouter Work Package 04 của Thanh. Có FastAPI, PostgreSQL, signer EIP-712 và worker quét event độc lập với checkout. Chưa tích hợp Shopify; chưa triển khai public.

## Trạng thái tích hợp demo hiện tại

Theo yêu cầu dùng deployment cũ, `.env` local bật `ALLOW_EXISTING_FUJI_DEMO=true`. Ngoại lệ chỉ có tác dụng trên chain 43113, Router `0xC83FA9cEcf7fc125dbeC893402c6f0f5e3f5AeE9` và MockUSDC `0xA61ABC464034161B4C5abAee7298c1503cF6E199`. Khóa demo chỉ được đọc từ bản repo Thanh đã tải về và lưu trong `.env` bị gitignore; không nhúng trong checkout mới. Quyền on-chain không bị sửa.

Merchant demo nhận MockUSDC là ví `0x18BaCa02E64F7Cdd9B888F9D2D67786bc0A15bc1`; không phải merchant production. Đã kiểm tra API ký intent có digest khớp contract thật bằng RPC read-only. Chưa gửi giao dịch mới hoặc nghiệm thu PAID end-to-end.

Tạo link demo 10 MockUSDC có hiệu lực một giờ: `docker compose exec api python -m app.demo_session`. Mở link bằng trình duyệt có MetaMask/Core Wallet, kết nối Fuji, nhận MockUSDC nếu thiếu, bấm Approve & Thanh toán. Ví buyer cần test AVAX. Trang `/checkout` là giao diện tích hợp mới dựa trên luồng connect/approve/pay đã kiểm tra trong repo Thanh, không phải xác nhận frontend riêng của Phú Đại đã được ghép.

`docker compose exec api python -m app.check_fuji` kiểm tra digest read-only và tạo một phiên kiểm tra riêng. Worker local bắt đầu từ block 61118412, được quan sát trước khi backend này tạo phiên đầu tiên; không import các giao dịch demo cũ thành đơn PAID.

## Chạy bằng Docker

1. Sao chép `.env.example` thành `.env`.
2. Sinh hai giá trị ngẫu nhiên khác nhau cho `MERCHANT_API_KEY` và `CHECKOUT_SECRET` (ví dụ `python -c "import secrets; print(secrets.token_hex(32))"`). Không đưa các khóa này vào frontend hoặc Git.
3. Điền `MERCHANT_ADDRESS`, `TOKEN_ADDRESS`, `ROUTER_ADDRESS`. Chỉ dùng ví merchant kiểm soát được, không dùng địa chỉ `0x…dEaD` của demo.
4. Chạy `docker compose up --build -d db api`.
5. Mở http://localhost:8000/docs. Health: `GET /health` kiểm tra kết nối database.

Database dùng tài khoản phát triển local, chỉ bind localhost. Trước khi đưa public phải thay tài khoản DB, HTTPS, giới hạn request, quản lý secrets và logging phù hợp.

## Bật ký và xác minh Fuji

- Thanh phải thay cả owner và signer đã lộ; backend cố ý từ chối owner/signer demo cũ.
- `SIGNER_PRIVATE_KEY` là khóa mới chỉ lưu server; public address phải trùng `backendSigner()` trên Router. Backend không gửi transaction và không cần giữ AVAX.
- `CHAIN_ID=43113`; điền địa chỉ token, Router và `START_BLOCK` bằng block deploy thực tế. Để 0 sẽ quét từ genesis, rất chậm.
- Chạy `docker compose --profile chain up --build -d worker` sau khi API đã khởi tạo schema.
- `docker compose logs -f worker` để xem tiến độ/lỗi. RPC lỗi: rollback cả batch và thử lại. Worker dùng checkpoint bền vững và khóa PostgreSQL để tránh hai worker xử lý đồng thời.
- Mặc định chờ 5 block (chính sách MVP có thể cấu hình, không phải bảo đảm chống mọi reorg). Nếu phát hiện lịch sử đã quét đổi hash, worker dừng để đối soát thủ công, không tự đảo các đơn PAID.

## API cho frontend và merchant server

### 1. Merchant server tạo phiên

`POST /api/payment-sessions`

Headers:
```
Authorization: Bearer <MERCHANT_API_KEY>
Idempotency-Key: <mã cố định của lần tạo checkout>
```
Body:
```json
{"order_ref":"demo-order-1","amount_units":"10000000","expires_in":1800}
```

`10000000` = 10 USDC với token 6 decimals. Backend dùng đơn vị nguyên, không nhận số float. Merchant server phải tính giá từ đơn hàng đáng tin cậy. API key là quyền tạo đơn, không giao cho browser. Địa chỉ merchant/token/Router lấy từ cấu hình server.

Cùng idempotency key và cùng body trả phiên cũ; body khác trả 409. Merchant integration phải tái sử dụng key theo đơn, vì hệ thống không tự cấm tạo nhiều phiên với `order_ref` giống nhau.

Response có `id`, `payment_id`, thông tin thanh toán và `checkout_token`. Token này là quyền truy cập riêng phiên, chuyển cho checkout qua kênh an toàn; tránh ghi log và URL. Giữ `CHECKOUT_SECRET` ổn định qua restart; đổi secret sẽ vô hiệu token cũ.

### 2. Checkout đọc phiên

`GET /api/payment-sessions/{id}` với `Authorization: Bearer <checkout_token>`.

### 3. Checkout yêu cầu intent

`POST /api/payment-sessions/{id}/intent`, cùng header, body:
```json
{"buyer":"0x...địa chỉ ví đã kết nối..."}
```
Response: `contractAddress`, `chainId`, `intent`, `signature`. Intent giữ nguyên paymentId, amount, merchant và deadline. Phiên bị khóa vào buyer đầu tiên; đổi buyer trả 409. Checkout token là bearer capability: người sở hữu token có thể gắn buyer, nên không chia sẻ token. Chưa có SIWE.

Frontend kiểm tra chain, allowance rồi approve đúng token/Router/amount và gọi `pay(intent, signature)`. Không ký intent trong browser. Đổi tài khoản sau khi đã gắn buyer cần xử lý rõ trên UI; không tự tạo phiên mới cho đơn đang có giao dịch.

### 4. Checkout hỏi trạng thái

`GET /api/payment-sessions/{id}/status`, cùng header. Poll mỗi 2–5 giây; `PAID` chỉ do worker ghi. Backend không nhận claim “đã trả” hoặc tin tx hash từ browser.

Trạng thái MVP: `PENDING`, `PAID`, `EXPIRED`. Reject hoặc transaction revert không làm phiên FAILED vĩnh viễn vì buyer có thể thử lại trước deadline. EXPIRED là trạng thái theo giờ hiện tại; worker vẫn có thể tìm được giao dịch hợp lệ đã vào block trước deadline và nâng thành PAID. Không coi EXPIRED là lý do tự tạo đơn thanh toán thứ hai ngay.

## Kiểm thử

`docker build -t universal-checkout-backend .`

`docker run --rm universal-checkout-backend python -m pytest -q`

Đã chạy 11/11 tests thành công trên SQLite và PostgreSQL 17 trong Docker (15/11/2026). Tests dùng signer test ngẫu nhiên và bằng chứng RPC giả lập; không dùng khóa demo, không gửi giao dịch Fuji. Bao gồm chữ ký EIP-712, auth, idempotency, buyer binding, evidence mismatch, expiry, retry/checkpoint và phát hiện reorg. Chưa test end-to-end với ví và Router Fuji mới. Các file trong `tests/` không phải khóa hay cấu hình production.

Để chạy bộ test với PostgreSQL, đặt `TEST_DATABASE_URL` vào **database riêng chỉ dành cho test**. Fixture xóa và tạo lại bảng mỗi test, tuyệt đối không dùng database có dữ liệu thật. Bản chạy Docker dùng các phiên bản cố định trong `requirements-lock.txt` (Linux/Python 3.13).

## Chạy Python trực tiếp (nếu không dùng Docker)

Python 3.13: tạo virtual environment, `pip install -r requirements.txt`, cấu hình `.env`, khởi động PostgreSQL, chạy `python -m app.init_db`, sau đó `uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`. Worker chạy ở terminal riêng: `python -m app.worker`.

## Giới hạn bàn giao

- `init_db` tạo schema mới, không migrate schema cũ. Trước khi sửa schema có dữ liệu cần bổ sung migration có version và backup.
- Không có Shopify adapter, webhook fulfillment, refund, multi-merchant hoặc quản trị đơn hàng.
- Worker nhận event từ Router tin cậy và đối chiếu receipt thành công, canonical block, paymentId, buyer, merchant, token, amount và deadline. Không hỗ trợ fee-on-transfer token.
- Thay Router: dừng tạo phiên cũ, đối soát hết phiên đang chạy trước khi chuyển cấu hình. Worker hiện quét một Router mỗi tiến trình.
- Không có auto-recovery deep reorg; cần vận hành đối soát. Không chạy public trước khi có giám sát worker, secrets manager và chính sách retry/fulfillment.

Tham khảo API thư viện: https://eth-account.readthedocs.io/en/stable/eth_account.html và https://web3py.readthedocs.io/en/stable/web3.eth.html.
