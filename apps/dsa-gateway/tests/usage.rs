mod common;

use axum::http::StatusCode;
use chrono::Duration;
use common::*;
use serde_json::{json, Value};

const SUMMARY_KEYS: [&str; 9] = [
    "period",
    "from_date",
    "to_date",
    "total_calls",
    "total_prompt_tokens",
    "total_completion_tokens",
    "total_tokens",
    "by_call_type",
    "by_model",
];

fn row(
    call_type: &'static str,
    model: &'static str,
    stock: Option<&'static str>,
    p: i64,
    c: i64,
    called_at: String,
) -> SeedRow {
    SeedRow {
        call_type,
        model,
        stock_code: stock,
        prompt: p,
        completion: c,
        total: p + c,
        called_at,
    }
}

/// Rows relative to "now" in Beijing time:
/// ids 1-4 this month (today), id 5 last month, id 6 in the future.
fn seeded_db() -> std::path::PathBuf {
    let last_month = sql(month_start() - Duration::days(1));
    let future = sql(beijing_now() + Duration::days(2));
    create_db(&[
        // id 1: exactly midnight -> `.000000`
        row("analysis", "gpt-a", Some("600519"), 100, 50, today_plus_micros(0)),
        // id 2
        row("agent", "gpt-b", None, 1000, 500, today_plus_micros(2)),
        // id 3
        row("analysis", "gpt-b", Some("AAPL"), 200, 100, today_plus_micros(3)),
        // id 4: same called_at as id 3 -> tie broken by id DESC
        row("market_review", "gpt-a", None, 10, 5, today_plus_micros(3)),
        // id 5: before this month
        row("analysis", "gpt-old", None, 7000, 7000, last_month),
        // id 6: in the future, never inside [from, now]
        row("analysis", "gpt-a", None, 9, 9, future),
    ])
}

async fn gateway_for(db: std::path::PathBuf) -> String {
    let (backend, _stub) = start_stub(AuthMode::Disabled).await;
    start_gateway(&format!("http://{backend}"), db).await
}

fn assert_month_summary(body: &Value) {
    assert_eq!(keys(body)[..], SUMMARY_KEYS[..]);
    assert_eq!(body["period"], "month");
    assert_eq!(
        body["from_date"],
        month_start().date().format("%Y-%m-%d").to_string()
    );
    assert_eq!(
        body["to_date"],
        beijing_now().date().format("%Y-%m-%d").to_string()
    );
    assert_eq!(body["total_calls"], 4);
    assert_eq!(body["total_prompt_tokens"], 1310);
    assert_eq!(body["total_completion_tokens"], 655);
    assert_eq!(body["total_tokens"], 1965);
    assert_eq!(
        body["by_call_type"],
        json!([
            {"call_type": "agent", "calls": 1, "prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            {"call_type": "analysis", "calls": 2, "prompt_tokens": 300, "completion_tokens": 150, "total_tokens": 450},
            {"call_type": "market_review", "calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ])
    );
    assert_eq!(
        body["by_model"],
        json!([
            {"model": "gpt-b", "calls": 2, "prompt_tokens": 1200, "completion_tokens": 600, "total_tokens": 1800, "max_total_tokens": 1500},
            {"model": "gpt-a", "calls": 2, "prompt_tokens": 110, "completion_tokens": 55, "total_tokens": 165, "max_total_tokens": 150},
        ])
    );
    assert_eq!(
        keys(&body["by_call_type"][0]),
        ["call_type", "calls", "prompt_tokens", "completion_tokens", "total_tokens"]
    );
    assert_eq!(
        keys(&body["by_model"][0]),
        ["model", "calls", "prompt_tokens", "completion_tokens", "total_tokens", "max_total_tokens"]
    );
}

#[tokio::test]
async fn summary_month_default_and_unknown_period() {
    let gw = gateway_for(seeded_db()).await;
    for q in ["", "?period=month", "?period=bogus", "?period=TODAY"] {
        let (status, body) = get_json(&format!("{gw}/api/v1/usage/summary{q}")).await;
        assert_eq!(status, StatusCode::OK, "{q}");
        assert_month_summary(&body);
    }
}

#[tokio::test]
async fn summary_all_includes_older_rows() {
    let gw = gateway_for(seeded_db()).await;
    let (status, body) = get_json(&format!("{gw}/api/v1/usage/summary?period=all")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["period"], "all");
    assert_eq!(body["from_date"], "2000-01-01");
    assert_eq!(body["total_calls"], 5);
    assert_eq!(body["total_tokens"], 1965 + 14000);
    assert_eq!(body["by_model"][0]["model"], "gpt-old");
    assert_eq!(body["by_model"][0]["max_total_tokens"], 14000);
    assert_eq!(body["by_call_type"][0]["call_type"], "analysis");
    assert_eq!(body["by_call_type"][0]["calls"], 3);
}

#[tokio::test]
async fn summary_today() {
    let gw = gateway_for(seeded_db()).await;
    let (status, body) = get_json(&format!("{gw}/api/v1/usage/summary?period=today")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["period"], "today");
    assert_eq!(
        body["from_date"],
        today_start().date().format("%Y-%m-%d").to_string()
    );
    assert_eq!(body["total_calls"], 4);
}

#[tokio::test]
async fn summary_empty_table() {
    let gw = gateway_for(create_db(&[])).await;
    let (status, body) = get_json(&format!("{gw}/api/v1/usage/summary")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(keys(&body)[..], SUMMARY_KEYS[..]);
    assert_eq!(body["total_calls"], 0);
    assert_eq!(body["total_prompt_tokens"], 0);
    assert_eq!(body["total_completion_tokens"], 0);
    assert_eq!(body["total_tokens"], 0);
    assert_eq!(body["by_call_type"], json!([]));
    assert_eq!(body["by_model"], json!([]));

    let (status, body) = get_json(&format!("{gw}/api/v1/usage/dashboard")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["recent_calls"], json!([]));
}

#[tokio::test]
async fn dashboard_recent_calls_order_limit_and_format() {
    let gw = gateway_for(seeded_db()).await;
    let (status, body) = get_json(&format!("{gw}/api/v1/usage/dashboard")).await;
    assert_eq!(status, StatusCode::OK);
    let mut expected_keys: Vec<&str> = SUMMARY_KEYS.to_vec();
    expected_keys.push("recent_calls");
    assert_eq!(keys(&body), expected_keys);
    assert_month_summary(&{
        let mut b = body.clone();
        b.as_object_mut().unwrap().remove("recent_calls");
        b
    });

    let recent = body["recent_calls"].as_array().unwrap();
    let ids: Vec<i64> = recent.iter().map(|r| r["id"].as_i64().unwrap()).collect();
    assert_eq!(ids, [4, 3, 2, 1]);

    let day = today_start().format("%Y-%m-%dT%H:%M:%S").to_string();
    assert_eq!(recent[0]["called_at"], format!("{day}.000003"));
    assert_eq!(recent[2]["called_at"], format!("{day}.000002"));
    // `.000000` stored -> fraction omitted, like Python isoformat().
    assert_eq!(recent[3]["called_at"], day);

    assert_eq!(
        keys(&recent[0]),
        [
            "id",
            "called_at",
            "call_type",
            "model",
            "stock_code",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens"
        ]
    );
    assert_eq!(
        recent[1],
        json!({"id": 3, "called_at": format!("{day}.000003"), "call_type": "analysis",
               "model": "gpt-b", "stock_code": "AAPL", "prompt_tokens": 200,
               "completion_tokens": 100, "total_tokens": 300})
    );
    assert_eq!(recent[0]["stock_code"], Value::Null);

    let (status, body) = get_json(&format!("{gw}/api/v1/usage/dashboard?limit=2")).await;
    assert_eq!(status, StatusCode::OK);
    let ids: Vec<i64> = body["recent_calls"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["id"].as_i64().unwrap())
        .collect();
    assert_eq!(ids, [4, 3]);
    // Totals are not affected by limit.
    assert_eq!(body["total_calls"], 4);

    let (status, body) =
        get_json(&format!("{gw}/api/v1/usage/dashboard?period=all&limit=200")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["recent_calls"].as_array().unwrap().len(), 5);
    assert_eq!(body["recent_calls"][4]["id"], 5);
}

#[tokio::test]
async fn dashboard_called_at_unparsable_is_returned_unchanged() {
    // Lexically inside [2000-01-01 ..., now] for period=all, but not a valid datetime.
    let weird = "2001-13-45 99:99:99".to_string();
    let db = create_db(&[SeedRow {
        call_type: "analysis",
        model: "m",
        stock_code: None,
        prompt: 1,
        completion: 1,
        total: 2,
        called_at: weird.clone(),
    }]);
    let gw = gateway_for(db).await;
    let (status, body) = get_json(&format!("{gw}/api/v1/usage/dashboard?period=all")).await;
    assert_eq!(status, StatusCode::OK);
    let recent = body["recent_calls"].as_array().unwrap();
    assert_eq!(recent.len(), 1);
    assert_eq!(recent[0]["called_at"], weird);
}

#[tokio::test]
async fn dashboard_limit_validation() {
    let gw = gateway_for(seeded_db()).await;
    for bad in ["0", "201", "abc", "-1", "1.5", ""] {
        let (status, body) =
            get_json(&format!("{gw}/api/v1/usage/dashboard?limit={bad}")).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "limit={bad}");
        assert_eq!(keys(&body), ["error", "message", "detail"]);
        assert_eq!(body["error"], "validation_error");
        assert_eq!(body["message"], "请求参数验证失败");
        let detail = body["detail"].as_array().unwrap();
        assert!(!detail.is_empty());
        for d in detail {
            assert_eq!(d["loc"], json!(["query", "limit"]));
            assert!(d["msg"].is_string());
            assert!(d["type"].is_string());
        }
    }
    for ok in ["1", "200", "50"] {
        let (status, _) = get_json(&format!("{gw}/api/v1/usage/dashboard?limit={ok}")).await;
        assert_eq!(status, StatusCode::OK, "limit={ok}");
    }
}

#[tokio::test]
async fn database_failures_return_500() {
    let expected = json!({"error": "internal_error", "message": "服务器内部错误", "detail": null});

    // Nonexistent file (must not be created by the gateway).
    let missing = temp_db_path();
    let gw = gateway_for(missing.clone()).await;
    for path in ["/api/v1/usage/summary", "/api/v1/usage/dashboard"] {
        let (status, body) = get_json(&format!("{gw}{path}")).await;
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR, "{path}");
        assert_eq!(body, expected);
        assert_eq!(keys(&body), ["error", "message", "detail"]);
    }
    assert!(!missing.exists(), "read-only open must not create the file");

    // Existing file without the table.
    let gw = gateway_for(create_empty_db_file()).await;
    let (status, body) = get_json(&format!("{gw}/api/v1/usage/summary")).await;
    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(body, expected);
}
