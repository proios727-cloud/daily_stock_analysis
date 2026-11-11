//! Shared test harness: a stub backend, a gateway on an ephemeral port and a
//! temporary SQLite database.
#![allow(dead_code)]

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::{Body, Bytes};
use axum::extract::{Request, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use chrono::{Datelike, Duration as ChronoDuration, FixedOffset, NaiveDate, NaiveDateTime, Utc};
use rusqlite::Connection;
use tokio::net::TcpListener;
use tokio::sync::Notify;

/// What the stub's `/api/v1/auth/status` answers.
#[derive(Clone, Copy, Debug)]
pub enum AuthMode {
    Disabled,
    LoggedIn,
    LoggedOut,
    ServerError,
    Garbage,
}

#[derive(Default)]
pub struct StubRecord {
    /// Cookie header values seen by the auth status endpoint.
    pub auth_cookies: Vec<Option<String>>,
    /// Number of auth status calls.
    pub auth_calls: usize,
}

pub struct StubState {
    pub mode: AuthMode,
    pub record: Mutex<StubRecord>,
    /// Released by the test to let `/stream` send its second chunk.
    pub stream_gate: Notify,
}

pub type Stub = Arc<StubState>;

async fn auth_status(State(st): State<Stub>, headers: HeaderMap) -> Response {
    {
        let mut rec = st.record.lock().unwrap();
        rec.auth_calls += 1;
        rec.auth_cookies.push(
            headers
                .get("cookie")
                .map(|v| v.to_str().unwrap().to_string()),
        );
    }
    let json = |s: &'static str| ([("content-type", "application/json")], s).into_response();
    match st.mode {
        AuthMode::Disabled => json(r#"{"authEnabled":false,"loggedIn":false,"passwordSet":false}"#),
        AuthMode::LoggedIn => json(r#"{"authEnabled":true,"loggedIn":true,"passwordSet":true}"#),
        AuthMode::LoggedOut => json(r#"{"authEnabled":true,"loggedIn":false,"passwordSet":true}"#),
        AuthMode::ServerError => (StatusCode::INTERNAL_SERVER_ERROR, "boom").into_response(),
        AuthMode::Garbage => json("not json"),
    }
}

/// Echoes the request as JSON with status 418, a custom header and two cookies.
async fn echo(req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let body = axum::body::to_bytes(body, usize::MAX).await.unwrap();
    let header = |n: &str| {
        parts
            .headers
            .get(n)
            .map(|v| v.to_str().unwrap().to_string())
    };
    let payload = serde_json::json!({
        "method": parts.method.as_str(),
        "path": parts.uri.path(),
        "query": parts.uri.query(),
        "x_custom": header("x-custom"),
        "content_length": header("content-length"),
        "transfer_encoding": header("transfer-encoding"),
        "host": header("host"),
        "x_forwarded_for": header("x-forwarded-for"),
        "x_forwarded_host": header("x-forwarded-host"),
        "x_forwarded_proto": header("x-forwarded-proto"),
        "connection_header": header("x-hop"),
        "body": String::from_utf8_lossy(&body),
    });
    Response::builder()
        .status(418)
        .header("content-type", "application/json")
        .header("x-stub-response", "yes")
        .header("set-cookie", "a=1; Path=/")
        .header("set-cookie", "b=2; Path=/; HttpOnly")
        .body(Body::from(payload.to_string()))
        .unwrap()
}

async fn redirect() -> Response {
    Response::builder()
        .status(302)
        .header("location", "/elsewhere")
        .body(Body::empty())
        .unwrap()
}

async fn stream(State(st): State<Stub>) -> Response {
    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Bytes, std::io::Error>>(4);
    tokio::spawn(async move {
        let _ = tx.send(Ok(Bytes::from_static(b"data: first\n\n"))).await;
        // Wait until the test has seen the first chunk (or give up after a while).
        let _ = tokio::time::timeout(Duration::from_secs(10), st.stream_gate.notified()).await;
        tokio::time::sleep(Duration::from_millis(50)).await;
        let _ = tx.send(Ok(Bytes::from_static(b"data: second\n\n"))).await;
    });
    let body_stream = futures_stream(rx);
    Response::builder()
        .status(200)
        .header("content-type", "text/event-stream")
        .body(Body::from_stream(body_stream))
        .unwrap()
}

/// Turn an mpsc receiver into a body stream.
fn futures_stream(
    rx: tokio::sync::mpsc::Receiver<Result<Bytes, std::io::Error>>,
) -> impl futures_util::Stream<Item = Result<Bytes, std::io::Error>> + Send + 'static {
    futures_util::stream::unfold(rx, |mut rx| async move {
        rx.recv().await.map(|item| (item, rx))
    })
}

pub async fn start_stub(mode: AuthMode) -> (SocketAddr, Stub) {
    let state = Arc::new(StubState {
        mode,
        record: Mutex::new(StubRecord::default()),
        stream_gate: Notify::new(),
    });
    let app = Router::new()
        .route("/api/v1/auth/status", get(auth_status))
        .route("/redirect", get(redirect))
        .route("/stream", get(stream))
        .fallback(echo)
        .with_state(state.clone());
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    (addr, state)
}

/// An address on which nothing listens.
pub fn dead_backend() -> String {
    let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = l.local_addr().unwrap();
    drop(l);
    format!("http://{addr}")
}

pub async fn start_gateway(backend_url: &str, db: PathBuf) -> String {
    let config = dsa_gateway::Config::new("127.0.0.1", 0, backend_url, db).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        dsa_gateway::serve(listener, config).await.unwrap();
    });
    format!("http://{addr}")
}

pub fn client() -> reqwest::Client {
    reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .unwrap()
}

static COUNTER: AtomicUsize = AtomicUsize::new(0);

/// A unique, not-yet-existing path under the system temp dir.
pub fn temp_db_path() -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "dsa-gateway-test-{}-{}-{}.db",
        std::process::id(),
        n,
        nanos
    ))
}

pub struct SeedRow {
    pub call_type: &'static str,
    pub model: &'static str,
    pub stock_code: Option<&'static str>,
    pub prompt: i64,
    pub completion: i64,
    pub total: i64,
    pub called_at: String,
}

/// Create a database with the `llm_usage` table (SQLAlchemy-like schema) and rows.
pub fn create_db(rows: &[SeedRow]) -> PathBuf {
    let path = temp_db_path();
    let conn = Connection::open(&path).unwrap();
    conn.execute_batch(
        "CREATE TABLE llm_usage (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            call_type VARCHAR(32) NOT NULL,
            model VARCHAR(128) NOT NULL,
            stock_code VARCHAR(16),
            provider VARCHAR(64),
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            called_at DATETIME NOT NULL
        );",
    )
    .unwrap();
    for r in rows {
        conn.execute(
            "INSERT INTO llm_usage (call_type, model, stock_code, prompt_tokens, \
             completion_tokens, total_tokens, called_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params![
                r.call_type,
                r.model,
                r.stock_code,
                r.prompt,
                r.completion,
                r.total,
                r.called_at
            ],
        )
        .unwrap();
    }
    path
}

/// An existing SQLite file without the `llm_usage` table.
pub fn create_empty_db_file() -> PathBuf {
    let path = temp_db_path();
    let conn = Connection::open(&path).unwrap();
    conn.execute_batch("CREATE TABLE other (x INTEGER);").unwrap();
    path
}

pub fn beijing_now() -> NaiveDateTime {
    Utc::now()
        .with_timezone(&FixedOffset::east_opt(8 * 3600).unwrap())
        .naive_local()
}

pub fn today_start() -> NaiveDateTime {
    beijing_now().date().and_hms_opt(0, 0, 0).unwrap()
}

pub fn month_start() -> NaiveDateTime {
    let now = beijing_now();
    NaiveDate::from_ymd_opt(now.year(), now.month(), 1)
        .unwrap()
        .and_hms_opt(0, 0, 0)
        .unwrap()
}

/// Today's midnight plus `micros` microseconds, in SQLAlchemy text format.
/// Tiny offsets keep these rows in the past unless the test runs in the first
/// few microseconds of the day.
pub fn today_plus_micros(micros: i64) -> String {
    sql(today_start() + ChronoDuration::microseconds(micros))
}

pub fn sql(dt: NaiveDateTime) -> String {
    dt.format("%Y-%m-%d %H:%M:%S%.6f").to_string()
}

pub async fn get_json(url: &str) -> (StatusCode, serde_json::Value) {
    let resp = client().get(url).send().await.unwrap();
    let status = StatusCode::from_u16(resp.status().as_u16()).unwrap();
    let text = resp.text().await.unwrap();
    let value = serde_json::from_str(&text)
        .unwrap_or_else(|e| panic!("body is not JSON ({e}): {text}"));
    (status, value)
}

/// Keys of a JSON object, in order.
pub fn keys(v: &serde_json::Value) -> Vec<String> {
    v.as_object().unwrap().keys().cloned().collect()
}
