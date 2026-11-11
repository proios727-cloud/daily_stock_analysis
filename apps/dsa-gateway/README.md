# dsa-gateway (pilot)

A small Rust HTTP gateway that sits in front of the Python FastAPI backend.
It is the first step of moving the API to Rust one route group at a time:

- **Served natively**
  - `GET /health`, `GET /api/health`, `GET /api/v1/health` – never call the backend.
  - `GET /api/v1/usage/summary`, `GET /api/v1/usage/dashboard` – read the
    `llm_usage` SQLite table directly (read-only), same response contract as
    `api/v1/endpoints/usage.py`. Auth is delegated to the backend: the gateway
    calls `GET /api/v1/auth/status` with the caller's `Cookie` header and fails
    closed (401 when login is required, 502 when the backend cannot answer).
- **Everything else** (other methods on the paths above included) is streamed
  through to the backend unchanged: method, path, query, headers (minus
  hop-by-hop), body. Responses (status, headers including repeated
  `Set-Cookie`, body, SSE) stream back; redirects are not followed.
  `X-Forwarded-For/-Host/-Proto` are added. Backend unreachable -> 502.

## Configuration

Read once at startup from the environment:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GATEWAY_HOST` | `127.0.0.1` | Bind host |
| `GATEWAY_PORT` | `8080` | Bind port |
| `GATEWAY_BACKEND_URL` | `http://127.0.0.1:8000` | Python backend base URL |
| `DATABASE_PATH` | `./data/stock_analysis.db` | SQLite file (same variable/default as the Python app) |

## Run

```bash
cd apps/dsa-gateway
cargo build --release
# start the Python backend separately (e.g. python main.py --serve-only), then:
GATEWAY_PORT=8080 DATABASE_PATH=../../data/stock_analysis.db ./target/release/dsa-gateway
curl http://127.0.0.1:8080/health
```

Note that `DATABASE_PATH` is resolved relative to the gateway's working
directory; point it at the same file the Python app uses.

## Test

```bash
cd apps/dsa-gateway
cargo test                                # offline; uses a stub backend + temp SQLite
cargo clippy --all-targets -- -D warnings
```

Integration tests live in `tests/` (health, usage, auth, proxy) and start the
gateway on an ephemeral port against an in-process axum stub backend.

## Known differences from calling the Python backend directly

- **Client address.** The backend sees every request as coming from the
  gateway. The gateway appends the real client IP to `X-Forwarded-For`, but the
  backend only reads it when `TRUST_X_FORWARDED_FOR=true`. Without that
  setting, login rate limiting counts all clients as one address.
- **Health.** The gateway's health routes report that the gateway is alive,
  not that the backend is.
- **Dot segments.** The HTTP client normalises paths before forwarding, so
  `/a/./b` and `/%2e%2e/x` reach the backend as `/a/b` and `/x`. The backend's
  auth middleware sees the normalised path, so this does not bypass auth.
- **`OPTIONS *`** returns 502 from the gateway (404 from the backend).
- **`Host`.** The backend receives its own host; the original is sent as
  `X-Forwarded-Host`.
- A request with both `Content-Length` and `Transfer-Encoding: chunked` is
  re-framed and forwarded; the backend alone would reject it with 400.

Not covered by tests: HTTP/2, a real Server-Sent Events stream from the
backend, and a real logged-in session (auth is tested against a stub backend).

`scripts/parity_check.py` compares the gateway with a running backend request
by request; see `docs/rust-gateway-pilot.md`.
