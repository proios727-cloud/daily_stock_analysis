//! Auth gate for natively served protected routes.
//!
//! The Python backend stays the single source of truth for sessions: we ask
//! `GET /api/v1/auth/status` with the caller's cookies and fail closed.

use std::time::Duration;

use axum::http::{header::COOKIE, HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::Value;

use crate::errors::{bad_gateway, unauthorized};
use crate::AppState;

/// Upper bound for the auth status call (the proxy itself has no request timeout).
const AUTH_TIMEOUT: Duration = Duration::from_secs(10);

/// Returns `Ok(())` when the request may proceed, otherwise the error response.
pub async fn require_login(state: &AppState, headers: &HeaderMap) -> Result<(), Response> {
    let url = format!("{}/api/v1/auth/status", state.config.backend_url);
    let mut req = state.client.get(&url).timeout(AUTH_TIMEOUT);
    for value in headers.get_all(COOKIE) {
        req = req.header(COOKIE, value.clone());
    }

    let resp = match req.send().await {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[dsa-gateway] auth status request failed: {e}");
            return Err(bad_gateway());
        }
    };
    if resp.status() != StatusCode::OK {
        eprintln!("[dsa-gateway] auth status returned HTTP {}", resp.status());
        return Err(bad_gateway());
    }
    let body: Value = match resp
        .bytes()
        .await
        .map_err(|e| e.to_string())
        .and_then(|b| serde_json::from_slice(&b).map_err(|e| e.to_string()))
    {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[dsa-gateway] auth status body is not valid JSON: {e}");
            return Err(bad_gateway());
        }
    };

    match body.get("authEnabled").and_then(Value::as_bool) {
        Some(false) => Ok(()),
        Some(true) => match body.get("loggedIn").and_then(Value::as_bool) {
            Some(true) => Ok(()),
            Some(false) => Err(unauthorized()),
            None => {
                eprintln!("[dsa-gateway] auth status body has no boolean loggedIn: {body}");
                Err(bad_gateway())
            }
        },
        None => {
            eprintln!("[dsa-gateway] auth status body has no boolean authEnabled: {body}");
            Err(bad_gateway())
        }
    }
}
