mod common;

use axum::http::StatusCode;
use common::*;
use serde_json::json;

const PATHS: [&str; 2] = ["/api/v1/usage/summary", "/api/v1/usage/dashboard"];

async fn get_with_cookie(url: &str, cookie: Option<&str>) -> (StatusCode, serde_json::Value) {
    let mut req = client().get(url);
    if let Some(c) = cookie {
        req = req.header("cookie", c);
    }
    let resp = req.send().await.unwrap();
    let status = StatusCode::from_u16(resp.status().as_u16()).unwrap();
    let body = serde_json::from_str(&resp.text().await.unwrap()).unwrap();
    (status, body)
}

#[tokio::test]
async fn logged_out_gets_401() {
    let (backend, stub) = start_stub(AuthMode::LoggedOut).await;
    let gw = start_gateway(&format!("http://{backend}"), create_db(&[])).await;
    for path in PATHS {
        let (status, body) = get_with_cookie(&format!("{gw}{path}"), Some("dsa_session=bad")).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "{path}");
        assert_eq!(body, json!({"error": "unauthorized", "message": "Login required"}));
        assert_eq!(keys(&body), ["error", "message"]);
    }
    // Validation happens after auth, as in the Python app (middleware first).
    let (status, _) =
        get_with_cookie(&format!("{gw}/api/v1/usage/dashboard?limit=0"), None).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(stub.record.lock().unwrap().auth_calls, 3);
}

#[tokio::test]
async fn logged_in_passes_and_cookie_is_forwarded() {
    let (backend, stub) = start_stub(AuthMode::LoggedIn).await;
    let gw = start_gateway(&format!("http://{backend}"), create_db(&[])).await;
    let cookie = "dsa_session=abc.def; other=1";
    for path in PATHS {
        let (status, _) = get_with_cookie(&format!("{gw}{path}"), Some(cookie)).await;
        assert_eq!(status, StatusCode::OK, "{path}");
    }
    let rec = stub.record.lock().unwrap();
    assert_eq!(
        rec.auth_cookies,
        vec![Some(cookie.to_string()), Some(cookie.to_string())]
    );
}

#[tokio::test]
async fn auth_disabled_passes_without_cookie() {
    let (backend, stub) = start_stub(AuthMode::Disabled).await;
    let gw = start_gateway(&format!("http://{backend}"), create_db(&[])).await;
    for path in PATHS {
        let (status, _) = get_with_cookie(&format!("{gw}{path}"), None).await;
        assert_eq!(status, StatusCode::OK, "{path}");
    }
    assert_eq!(stub.record.lock().unwrap().auth_cookies, vec![None, None]);
}

#[tokio::test]
async fn auth_backend_failures_fail_closed_with_502() {
    let expected = json!({"error": "bad_gateway", "message": "Backend unavailable", "detail": null});

    let gw = start_gateway(&dead_backend(), create_db(&[])).await;
    for path in PATHS {
        let (status, body) = get_with_cookie(&format!("{gw}{path}"), None).await;
        assert_eq!(status, StatusCode::BAD_GATEWAY, "down {path}");
        assert_eq!(body, expected);
    }

    for mode in [AuthMode::ServerError, AuthMode::Garbage] {
        let (backend, _stub) = start_stub(mode).await;
        let gw = start_gateway(&format!("http://{backend}"), create_db(&[])).await;
        for path in PATHS {
            let (status, body) = get_with_cookie(&format!("{gw}{path}"), None).await;
            assert_eq!(status, StatusCode::BAD_GATEWAY, "{mode:?} {path}");
            assert_eq!(body, expected);
        }
    }
}
