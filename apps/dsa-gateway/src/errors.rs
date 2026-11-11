//! JSON error responses shared by the native routes and the proxy.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};

/// 502 returned when the Python backend cannot be reached (or the auth check fails).
pub fn bad_gateway() -> Response {
    (
        StatusCode::BAD_GATEWAY,
        Json(json!({"error": "bad_gateway", "message": "Backend unavailable", "detail": null})),
    )
        .into_response()
}

/// 401 returned when the backend reports that a login is required.
pub fn unauthorized() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(json!({"error": "unauthorized", "message": "Login required"})),
    )
        .into_response()
}

/// 500 returned on database failures. The real error is logged by the caller.
pub fn internal_error() -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"error": "internal_error", "message": "服务器内部错误", "detail": null})),
    )
        .into_response()
}

/// 422 in the same envelope the Python app uses for request validation errors.
pub fn validation_error(detail: Vec<Value>) -> Response {
    (
        StatusCode::UNPROCESSABLE_ENTITY,
        Json(json!({"error": "validation_error", "message": "请求参数验证失败", "detail": detail})),
    )
        .into_response()
}
