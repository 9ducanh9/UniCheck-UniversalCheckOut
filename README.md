# UniCheck

UniCheck adds a wallet payment option to the cart of a Shopify development store. The hosted demo runs on Railway and uses MockUSDC on Avalanche Fuji (chain ID 43113).

## Demo

1. Open the [UniCheck Shopify dev store](https://unicheck-demo.myshopify.com/) and add a regular, one-time purchase product. The store may require its storefront password.
2. Open the cart drawer or the [full cart page](https://unicheck-demo.myshopify.com/cart). Choose **Thanh toán thử với UniCheck** below **Check out**.
3. Choose **Tạo phiên thanh toán** on the hosted page. The backend reads the price from Shopify, creates a draft order and opens Hosted Checkout. This step no longer asks for a separate UniCheck demo password.
4. Connect Core on Fuji, obtain test MockUSDC if needed, approve the exact amount and confirm the test payment. The buyer needs Fuji AVAX for gas.

The latest hosted checks confirmed draft order creation and wallet connection. A new payment reaching **PAID** on the current hosted deployment has yet to be verified. The demo uses a cart integration. A native Shopify Payments Extension requires Shopify approval.

## Source code and deployment

| Component | Source |
| --- | --- |
| Hosted Checkout and cart handoff | [checkout.html](app/checkout.html), [cart-demo.html](app/cart-demo.html) |
| Shopify cart button and draft-order integration | [Liquid example](shopify-cart-demo.liquid), [Shopify adapter](app/shopify.py) |
| FastAPI, EIP-712 signing and PostgreSQL | [API](app/main.py), [signing](app/chain.py), [database](app/db.py) |
| Fuji verification and Shopify reconciliation | [worker](app/worker.py), [Shopify adapter](app/shopify.py) |
| Solidity by Thanh | [PaymentRouter.sol](https://github.com/thanhhoang06/Check_out_blockchain/blob/ec689076276c65a9b64d6e5d76ba299cdcb2dc46/contracts/PaymentRouter.sol), [MockUSDC.sol](https://github.com/thanhhoang06/Check_out_blockchain/blob/ec689076276c65a9b64d6e5d76ba299cdcb2dc46/contracts/MockUSDC.sol) |

- Railway [backend health](https://backend-production-47a83b3c.up.railway.app/health). The deployment uses PostgreSQL and a separate Fuji worker.
- Fuji [PaymentRouter](https://testnet.snowtrace.io/address/0xC83FA9cEcf7fc125dbeC893402c6f0f5e3f5AeE9) and [MockUSDC](https://testnet.snowtrace.io/address/0xA61ABC464034161B4C5abAee7298c1503cF6E199).
- The backend signs the payment intent. The buyer signs the transaction. After verifying the receipt and matching the payment event, the worker records **PAID** and completes the Shopify draft order.

The Fuji deployment uses an existing demo Router and a signer that was previously exposed. Use it only with test tokens. Keep all Shopify credentials, API keys and signing keys in server-side environment variables.

## Chạy bằng Docker

1. Sao chép `.env.example` thành `.env`.
2. Sinh hai giá trị ngẫu nhiên khác nhau cho `MERCHANT_API_KEY` và `CHECKOUT_SECRET` (ví dụ `python -c "import secrets; print(secrets.token_hex(32))"`). Không đưa các khóa này vào frontend hoặc Git.
3. Điền `MERCHANT_ADDRESS`, `TOKEN_ADDRESS`, `ROUTER_ADDRESS`. Chỉ dùng ví merchant kiểm soát được, không dùng địa chỉ `0x…dEaD` của demo.
4. Chạy `docker compose up --build -d db api`.
5. Mở http://localhost:8000/docs. Health: `GET /health` kiểm tra kết nối database.

Database dùng tài khoản phát triển local, chỉ bind localhost. Trước khi đưa public phải thay tài khoản DB, HTTPS, giới hạn request, quản lý secrets và logging phù hợp.

## Bật ký và xác minh Fuji

- Với deployment mới, dùng owner và signer riêng. Ngoại lệ cho Router demo hiện tại chỉ áp dụng trên Fuji khi bật `ALLOW_EXISTING_FUJI_DEMO`.
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

`docker run --rm universal-checkout-backend python -m pytest -q tests`

Bộ test backend có 16 test đã chạy thành công, gồm xác minh thanh toán và tích hợp Shopify với dữ liệu giả lập.

Để chạy bộ test với PostgreSQL, đặt `TEST_DATABASE_URL` vào **database riêng chỉ dành cho test**. Fixture xóa và tạo lại bảng mỗi test, tuyệt đối không dùng database có dữ liệu thật. Bản chạy Docker dùng các phiên bản cố định trong `requirements-lock.txt` (Linux/Python 3.13).

## Chạy Python trực tiếp (nếu không dùng Docker)

Python 3.13: tạo virtual environment, `pip install -r requirements.txt`, cấu hình `.env`, khởi động PostgreSQL, chạy `python -m app.init_db`, sau đó `uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`. Worker chạy ở terminal riêng: `python -m app.worker`.

## Giới hạn bàn giao

- `init_db` tạo schema mới, không migrate schema cũ. Trước khi sửa schema có dữ liệu cần bổ sung migration có version và backup.
- Demo Shopify hỗ trợ tạo và hoàn tất draft order. Webhook fulfillment, refund, multi-merchant và quản trị đơn hàng nằm ngoài phạm vi hiện tại.
- Worker nhận event từ Router tin cậy và đối chiếu receipt thành công, canonical block, paymentId, buyer, merchant, token, amount và deadline. Không hỗ trợ fee-on-transfer token.
- Thay Router: dừng tạo phiên cũ, đối soát hết phiên đang chạy trước khi chuyển cấu hình. Worker hiện quét một Router mỗi tiến trình.
- Không có auto-recovery deep reorg; cần vận hành đối soát. Để chạy production cần giám sát worker, quản lý secrets và quy trình đối soát.

Tham khảo API thư viện: https://eth-account.readthedocs.io/en/stable/eth_account.html và https://web3py.readthedocs.io/en/stable/web3.eth.html.
