# Rust 网关试点（dsa-gateway）

> 状态：试点（pilot）。默认部署不使用它；Docker、桌面端与 `python main.py --serve` 的行为均未改变。

## 目的

验证“按路由组逐步把 API 从 Python 迁到 Rust”的可行性，而不是一次性重写。`apps/dsa-gateway/` 是一个放在现有 FastAPI 后端前面的 Rust（axum）网关：已迁移的路由由 Rust 直接处理，其余请求原样转发给 Python 后端。

## 当前范围

| 路由 | 处理方 | 说明 |
| --- | --- | --- |
| `GET /health`、`/api/health`、`/api/v1/health` | Rust | 不访问后端，不需要登录 |
| `GET /api/v1/usage/summary`、`/api/v1/usage/dashboard` | Rust | 只读方式直接查询 `DATABASE_PATH` 指向的 SQLite `llm_usage` 表，响应契约与 `api/v1/endpoints/usage.py` 一致 |
| 其余全部路由（含 Web 静态资源、SSE） | Python | 由网关流式反向代理 |

## 关键语义

- 认证：会话校验仍只在 Python 中实现。网关处理 usage 路由前，会携带请求的 `Cookie` 调用后端 `GET /api/v1/auth/status`；未登录返回 401，后端不可用时返回 502（fail closed）。
- 健康检查：网关的 `/health` 只表示网关进程存活，不代表 Python 后端可用。若将来把容器健康检查指向网关，需要先调整这一点。
- 数据库：网关直接读取 `llm_usage` 表，因此该表的列名或 `called_at` 存储格式变化时，必须同步修改 `apps/dsa-gateway/src/usage.rs`。仅支持 SQLite 文件。
- 数据库打开或查询失败时返回 500，不会用空结果掩盖错误。

## 配置

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `GATEWAY_HOST` | `127.0.0.1` | 监听地址 |
| `GATEWAY_PORT` | `8080` | 监听端口 |
| `GATEWAY_BACKEND_URL` | `http://127.0.0.1:8000` | Python 后端地址 |
| `DATABASE_PATH` | `./data/stock_analysis.db` | 与 Python 后端相同的 SQLite 文件 |

这些变量只被网关读取，不影响 Python 应用，因此未加入 `.env.example`。

## 运行与验证

```bash
# 1. 启动 Python 后端
python main.py --serve-only --host 127.0.0.1 --port 8000

# 2. 构建并启动网关（DATABASE_PATH 指向后端使用的同一个文件）
cd apps/dsa-gateway
cargo build --release
DATABASE_PATH=../../data/stock_analysis.db ./target/release/dsa-gateway

# 3. 测试
cargo test
cargo clippy --all-targets -- -D warnings
python3 scripts/parity_check.py   # 逐路由对比 Python 后端与网关的响应
```

## 尚未覆盖

- CI 中没有 Rust 构建/测试任务。
- 未接入 Docker 镜像、桌面端打包和发布流程。
- 其余路由组仍在 Python；是否继续迁移取决于试点评估。

## 回滚

删除 `apps/dsa-gateway/` 与本文档即可，Python 应用没有任何改动。
