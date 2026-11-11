//! Native health check; never touches the backend.

use axum::Json;
use serde_json::{json, Value};

use crate::timefmt::python_isoformat;

pub async fn health() -> Json<Value> {
    let now = chrono::Local::now().naive_local();
    Json(json!({"status": "ok", "timestamp": python_isoformat(&now)}))
}
