mod common;

use std::time::Duration;

use common::*;
use serde_json::{json, Value};

#[tokio::test]
async fn forwards_request_and_returns_response() {
    let (backend, _stub) = start_stub(AuthMode::Disabled).await;
    let gw = start_gateway(&format!("http://{backend}"), temp_db_path()).await;

    let resp = client()
        .post(format!("{gw}/api/v1/data/overview?a=1&b=two%20words"))
        .header("x-custom", "hello")
        .header("x-forwarded-for", "10.0.0.1")
        .header("connection", "x-hop")
        .header("x-hop", "should-be-stripped")
        .body("payload-body")
        .send()
        .await
        .unwrap();

    assert_eq!(resp.status().as_u16(), 418);
    assert_eq!(resp.headers()["x-stub-response"], "yes");
    let cookies: Vec<&str> = resp
        .headers()
        .get_all("set-cookie")
        .iter()
        .map(|v| v.to_str().unwrap())
        .collect();
    assert_eq!(cookies, ["a=1; Path=/", "b=2; Path=/; HttpOnly"]);

    let echo: Value = serde_json::from_str(&resp.text().await.unwrap()).unwrap();
    assert_eq!(echo["method"], "POST");
    assert_eq!(echo["path"], "/api/v1/data/overview");
    assert_eq!(echo["query"], "a=1&b=two%20words");
    assert_eq!(echo["x_custom"], "hello");
    assert_eq!(echo["body"], "payload-body");
    assert_eq!(echo["content_length"], "12");
    assert_eq!(echo["host"], backend.to_string());
    assert_eq!(echo["x_forwarded_for"], "10.0.0.1, 127.0.0.1");
    assert_eq!(
        echo["x_forwarded_host"],
        gw.trim_start_matches("http://")
    );
    assert_eq!(echo["x_forwarded_proto"], "http");
    assert_eq!(echo["connection_header"], Value::Null);
}

#[tokio::test]
async fn non_get_on_native_path_is_proxied() {
    let (backend, _stub) = start_stub(AuthMode::Disabled).await;
    let gw = start_gateway(&format!("http://{backend}"), temp_db_path()).await;
    let resp = client()
        .delete(format!("{gw}/api/v1/usage/summary"))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status().as_u16(), 418);
    let echo: Value = serde_json::from_str(&resp.text().await.unwrap()).unwrap();
    assert_eq!(echo["method"], "DELETE");
    assert_eq!(echo["path"], "/api/v1/usage/summary");
}

#[tokio::test]
async fn get_without_body_and_web_paths() {
    let (backend, _stub) = start_stub(AuthMode::Disabled).await;
    let gw = start_gateway(&format!("http://{backend}"), temp_db_path()).await;
    for path in ["/", "/docs", "/assets/index-abc.js", "/health/"] {
        let resp = client().get(format!("{gw}{path}")).send().await.unwrap();
        assert_eq!(resp.status().as_u16(), 418, "{path}");
        let echo: Value = serde_json::from_str(&resp.text().await.unwrap()).unwrap();
        assert_eq!(echo["method"], "GET");
        assert_eq!(echo["path"], path);
        assert_eq!(echo["body"], "");
        assert_eq!(echo["transfer_encoding"], Value::Null, "{path}");
    }
}

#[tokio::test]
async fn redirect_is_passed_through() {
    let (backend, _stub) = start_stub(AuthMode::Disabled).await;
    let gw = start_gateway(&format!("http://{backend}"), temp_db_path()).await;
    let resp = client().get(format!("{gw}/redirect")).send().await.unwrap();
    assert_eq!(resp.status().as_u16(), 302);
    assert_eq!(resp.headers()["location"], "/elsewhere");
}

#[tokio::test]
async fn streamed_response_arrives_incrementally() {
    let (backend, stub) = start_stub(AuthMode::Disabled).await;
    let gw = start_gateway(&format!("http://{backend}"), temp_db_path()).await;
    let mut resp = client().get(format!("{gw}/stream")).send().await.unwrap();
    assert_eq!(resp.status().as_u16(), 200);
    assert_eq!(resp.headers()["content-type"], "text/event-stream");

    // The stub only sends the second chunk after we release it, so receiving
    // the first chunk proves the gateway does not buffer the whole response.
    let first = tokio::time::timeout(Duration::from_secs(5), resp.chunk())
        .await
        .expect("first chunk not streamed through")
        .unwrap()
        .unwrap();
    assert_eq!(&first[..], b"data: first\n\n");
    stub.stream_gate.notify_one();

    let mut rest = Vec::new();
    while let Some(chunk) = tokio::time::timeout(Duration::from_secs(5), resp.chunk())
        .await
        .expect("stream stalled")
        .unwrap()
    {
        rest.extend_from_slice(&chunk);
    }
    assert_eq!(rest, b"data: second\n\n");
}

#[tokio::test]
async fn backend_down_returns_502() {
    let gw = start_gateway(&dead_backend(), temp_db_path()).await;
    for path in ["/api/v1/data/overview", "/", "/docs"] {
        let (status, body) = get_json(&format!("{gw}{path}")).await;
        assert_eq!(status.as_u16(), 502, "{path}");
        assert_eq!(
            body,
            json!({"error": "bad_gateway", "message": "Backend unavailable", "detail": null})
        );
    }
}
