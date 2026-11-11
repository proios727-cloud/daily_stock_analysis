//! dsa-gateway: a pilot Rust HTTP gateway in front of the Python FastAPI backend.
//!
//! A few routes are served natively (health, LLM usage); every other request is
//! reverse-proxied unchanged to `GATEWAY_BACKEND_URL`.

pub mod auth;
pub mod config;
pub mod errors;
pub mod health;
pub mod proxy;
pub mod timefmt;
pub mod usage;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::routing::get;
use axum::Router;
use tokio::net::TcpListener;

pub use config::Config;

/// Connect timeout for backend calls. There is deliberately no overall request
/// timeout on proxied requests (long analysis calls, SSE streams).
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub client: reqwest::Client,
}

/// HTTP client used for all backend calls: no redirects, no system proxy.
pub fn backend_client() -> reqwest::Client {
    reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .connect_timeout(CONNECT_TIMEOUT)
        .build()
        .expect("failed to build HTTP client")
}

/// Build the router. Native routes only claim GET (and HEAD); other methods on
/// those paths fall through to the proxy like any other request.
pub fn build_app(config: Config) -> Router {
    let state = AppState {
        config: Arc::new(config),
        client: backend_client(),
    };
    Router::new()
        .route("/health", get(health::health).fallback(proxy::proxy))
        .route("/api/health", get(health::health).fallback(proxy::proxy))
        .route("/api/v1/health", get(health::health).fallback(proxy::proxy))
        .route(
            "/api/v1/usage/summary",
            get(usage::summary).fallback(proxy::proxy),
        )
        .route(
            "/api/v1/usage/dashboard",
            get(usage::dashboard).fallback(proxy::proxy),
        )
        .fallback(proxy::proxy)
        .with_state(state)
}

/// Serve the gateway on an already bound listener (used by `main` and tests).
pub async fn serve(listener: TcpListener, config: Config) -> std::io::Result<()> {
    let app = build_app(config);
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await
}
