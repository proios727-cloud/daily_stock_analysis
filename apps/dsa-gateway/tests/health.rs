mod common;

use axum::http::StatusCode;
use chrono::NaiveDateTime;
use common::*;

#[tokio::test]
async fn health_paths_work_with_backend_down() {
    let gw = start_gateway(&dead_backend(), temp_db_path()).await;
    for path in ["/health", "/api/health", "/api/v1/health"] {
        let (status, body) = get_json(&format!("{gw}{path}")).await;
        assert_eq!(status, StatusCode::OK, "{path}");
        assert_eq!(body["status"], "ok", "{path}");
        assert_eq!(keys(&body), ["status", "timestamp"]);
        let ts = body["timestamp"].as_str().unwrap();
        assert!(!ts.ends_with('Z') && !ts.contains('+'), "no tz suffix: {ts}");
        NaiveDateTime::parse_from_str(ts, "%Y-%m-%dT%H:%M:%S%.f")
            .unwrap_or_else(|e| panic!("unparseable timestamp {ts}: {e}"));
    }
}
