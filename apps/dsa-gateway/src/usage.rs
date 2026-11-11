//! Native `/api/v1/usage/summary` and `/api/v1/usage/dashboard`, reading the
//! `llm_usage` SQLite table read-only (same semantics as `api/v1/endpoints/usage.py`).

use std::path::Path;
use std::time::Duration;

use axum::extract::rejection::QueryRejection;
use axum::extract::{Query, State};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::Json;
use chrono::{Datelike, FixedOffset, NaiveDate, NaiveDateTime, Utc};
use rusqlite::types::ValueRef;
use rusqlite::{params, Connection, OpenFlags};
use serde::Serialize;
use serde_json::{json, Value};

use crate::auth::require_login;
use crate::errors::{internal_error, validation_error};
use crate::timefmt::{stored_to_isoformat, to_sql_text};
use crate::AppState;

const DEFAULT_LIMIT: i64 = 50;
const MIN_LIMIT: i64 = 1;
const MAX_LIMIT: i64 = 200;
const BUSY_TIMEOUT: Duration = Duration::from_millis(5000);
/// Beijing time, fixed UTC+8 (no DST).
const BEIJING_OFFSET_SECS: i32 = 8 * 3600;

#[derive(Serialize)]
struct CallTypeRow {
    call_type: String,
    calls: i64,
    prompt_tokens: i64,
    completion_tokens: i64,
    total_tokens: i64,
}

#[derive(Serialize)]
struct ModelRow {
    model: String,
    calls: i64,
    prompt_tokens: i64,
    completion_tokens: i64,
    total_tokens: i64,
    max_total_tokens: i64,
}

#[derive(Serialize)]
struct RecentCall {
    id: i64,
    called_at: String,
    call_type: String,
    model: String,
    stock_code: Option<String>,
    prompt_tokens: i64,
    completion_tokens: i64,
    total_tokens: i64,
}

#[derive(Serialize)]
struct Summary {
    period: String,
    from_date: String,
    to_date: String,
    total_calls: i64,
    total_prompt_tokens: i64,
    total_completion_tokens: i64,
    total_tokens: i64,
    by_call_type: Vec<CallTypeRow>,
    by_model: Vec<ModelRow>,
}

#[derive(Serialize)]
struct Dashboard {
    #[serde(flatten)]
    summary: Summary,
    recent_calls: Vec<RecentCall>,
}

/// Normalized query: `today` | `month` | `all`; anything else becomes `month`.
fn normalize_period(raw: Option<&str>) -> &'static str {
    match raw {
        Some("today") => "today",
        Some("all") => "all",
        _ => "month",
    }
}

/// Current Beijing time as a naive datetime.
fn beijing_now() -> NaiveDateTime {
    let tz = FixedOffset::east_opt(BEIJING_OFFSET_SECS).expect("valid offset");
    Utc::now().with_timezone(&tz).naive_local()
}

/// `(from, to)` naive Beijing datetimes for a normalized period.
pub fn date_range(period: &str, now: NaiveDateTime) -> (NaiveDateTime, NaiveDateTime) {
    let from = match period {
        "today" => now.date().and_hms_opt(0, 0, 0),
        "all" => NaiveDate::from_ymd_opt(2000, 1, 1).and_then(|d| d.and_hms_opt(0, 0, 0)),
        _ => NaiveDate::from_ymd_opt(now.year(), now.month(), 1)
            .and_then(|d| d.and_hms_opt(0, 0, 0)),
    }
    .expect("valid start of range");
    (from, now)
}

/// Last value of each query parameter wins (as in FastAPI for scalar params).
fn last_param<'a>(pairs: &'a [(String, String)], name: &str) -> Option<&'a str> {
    pairs
        .iter()
        .rev()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

fn parse_limit(raw: Option<&str>) -> Result<i64, Value> {
    let Some(raw) = raw else {
        return Ok(DEFAULT_LIMIT);
    };
    let loc = json!(["query", "limit"]);
    let value: i64 = raw.trim().parse().map_err(|_| {
        json!({
            "type": "int_parsing",
            "loc": loc,
            "msg": "Input should be a valid integer, unable to parse string as an integer",
            "input": raw,
        })
    })?;
    if value < MIN_LIMIT {
        return Err(json!({
            "type": "greater_than_equal",
            "loc": loc,
            "msg": format!("Input should be greater than or equal to {MIN_LIMIT}"),
            "input": raw,
            "ctx": {"ge": MIN_LIMIT},
        }));
    }
    if value > MAX_LIMIT {
        return Err(json!({
            "type": "less_than_equal",
            "loc": loc,
            "msg": format!("Input should be less than or equal to {MAX_LIMIT}"),
            "input": raw,
            "ctx": {"le": MAX_LIMIT},
        }));
    }
    Ok(value)
}

fn query_pairs(
    query: Result<Query<Vec<(String, String)>>, QueryRejection>,
) -> Result<Vec<(String, String)>, Value> {
    match query {
        Ok(Query(pairs)) => Ok(pairs),
        Err(e) => Err(json!({
            "type": "query_parsing",
            "loc": ["query"],
            "msg": e.body_text(),
        })),
    }
}

pub async fn summary(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<Vec<(String, String)>>, QueryRejection>,
) -> Response {
    if let Err(resp) = require_login(&state, &headers).await {
        return resp;
    }
    let pairs = match query_pairs(query) {
        Ok(p) => p,
        Err(detail) => return validation_error(vec![detail]),
    };
    let period = normalize_period(last_param(&pairs, "period"));
    let db = state.config.database_path.clone();
    run_db(move || {
        let conn = open_db(&db)?;
        build_summary(&conn, period, beijing_now())
    })
    .await
}

pub async fn dashboard(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<Vec<(String, String)>>, QueryRejection>,
) -> Response {
    if let Err(resp) = require_login(&state, &headers).await {
        return resp;
    }
    let pairs = match query_pairs(query) {
        Ok(p) => p,
        Err(detail) => return validation_error(vec![detail]),
    };
    let limit = match parse_limit(last_param(&pairs, "limit")) {
        Ok(l) => l,
        Err(detail) => return validation_error(vec![detail]),
    };
    let period = normalize_period(last_param(&pairs, "period"));
    let db = state.config.database_path.clone();
    run_db(move || {
        let conn = open_db(&db)?;
        let now = beijing_now();
        let summary = build_summary(&conn, period, now)?;
        let (from, to) = date_range(period, now);
        let recent_calls = recent_calls(&conn, &from, &to, limit)?;
        Ok(Dashboard {
            summary,
            recent_calls,
        })
    })
    .await
}

/// Run a blocking DB closure off the async runtime; any failure becomes a logged 500.
async fn run_db<T, F>(f: F) -> Response
where
    T: Serialize + Send + 'static,
    F: FnOnce() -> rusqlite::Result<T> + Send + 'static,
{
    match tokio::task::spawn_blocking(f).await {
        Ok(Ok(body)) => Json(body).into_response(),
        Ok(Err(e)) => {
            eprintln!("[dsa-gateway] usage query failed: {e}");
            internal_error()
        }
        Err(e) => {
            eprintln!("[dsa-gateway] usage query task failed: {e}");
            internal_error()
        }
    }
}

fn open_db(path: &Path) -> rusqlite::Result<Connection> {
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.busy_timeout(BUSY_TIMEOUT)?;
    Ok(conn)
}

fn build_summary(
    conn: &Connection,
    period: &str,
    now: NaiveDateTime,
) -> rusqlite::Result<Summary> {
    let (from, to) = date_range(period, now);
    let from_s = to_sql_text(&from);
    let to_s = to_sql_text(&to);

    let (total_calls, total_prompt, total_completion, total_tokens) = conn.query_row(
        "SELECT COUNT(id), COALESCE(SUM(prompt_tokens), 0), \
                COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(total_tokens), 0) \
         FROM llm_usage WHERE called_at >= ?1 AND called_at <= ?2",
        params![from_s, to_s],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
    )?;

    let mut stmt = conn.prepare(
        "SELECT call_type, COUNT(id), COALESCE(SUM(prompt_tokens), 0), \
                COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(total_tokens), 0) \
         FROM llm_usage WHERE called_at >= ?1 AND called_at <= ?2 \
         GROUP BY call_type ORDER BY SUM(total_tokens) DESC",
    )?;
    let by_call_type = stmt
        .query_map(params![from_s, to_s], |r| {
            Ok(CallTypeRow {
                call_type: r.get(0)?,
                calls: r.get(1)?,
                prompt_tokens: r.get(2)?,
                completion_tokens: r.get(3)?,
                total_tokens: r.get(4)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    let mut stmt = conn.prepare(
        "SELECT model, COUNT(id), COALESCE(SUM(prompt_tokens), 0), \
                COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(total_tokens), 0), \
                COALESCE(MAX(total_tokens), 0) AS max_total_tokens \
         FROM llm_usage WHERE called_at >= ?1 AND called_at <= ?2 \
         GROUP BY model ORDER BY SUM(total_tokens) DESC",
    )?;
    let by_model = stmt
        .query_map(params![from_s, to_s], |r| {
            Ok(ModelRow {
                model: r.get(0)?,
                calls: r.get(1)?,
                prompt_tokens: r.get(2)?,
                completion_tokens: r.get(3)?,
                total_tokens: r.get(4)?,
                max_total_tokens: r.get(5)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    Ok(Summary {
        period: period.to_string(),
        from_date: from.date().format("%Y-%m-%d").to_string(),
        to_date: to.date().format("%Y-%m-%d").to_string(),
        total_calls,
        total_prompt_tokens: total_prompt,
        total_completion_tokens: total_completion,
        total_tokens,
        by_call_type,
        by_model,
    })
}

fn recent_calls(
    conn: &Connection,
    from: &NaiveDateTime,
    to: &NaiveDateTime,
    limit: i64,
) -> rusqlite::Result<Vec<RecentCall>> {
    let mut stmt = conn.prepare(
        "SELECT id, called_at, call_type, model, stock_code, \
                prompt_tokens, completion_tokens, total_tokens \
         FROM llm_usage WHERE called_at >= ?1 AND called_at <= ?2 \
         ORDER BY called_at DESC, id DESC LIMIT ?3",
    )?;
    let rows = stmt
        .query_map(params![to_sql_text(from), to_sql_text(to), limit], |r| {
            Ok(RecentCall {
                id: r.get(0)?,
                called_at: called_at_text(r.get_ref(1)?),
                call_type: r.get(2)?,
                model: r.get(3)?,
                stock_code: r.get(4)?,
                prompt_tokens: r.get(5)?,
                completion_tokens: r.get(6)?,
                total_tokens: r.get(7)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(rows)
}

fn called_at_text(value: ValueRef<'_>) -> String {
    match value {
        ValueRef::Text(bytes) => stored_to_isoformat(&String::from_utf8_lossy(bytes)),
        ValueRef::Integer(i) => i.to_string(),
        ValueRef::Real(f) => f.to_string(),
        ValueRef::Null => String::new(),
        ValueRef::Blob(bytes) => String::from_utf8_lossy(bytes).into_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn period_normalization() {
        assert_eq!(normalize_period(None), "month");
        assert_eq!(normalize_period(Some("today")), "today");
        assert_eq!(normalize_period(Some("all")), "all");
        assert_eq!(normalize_period(Some("TODAY")), "month");
        assert_eq!(normalize_period(Some("week")), "month");
    }

    #[test]
    fn ranges() {
        let now = NaiveDate::from_ymd_opt(2026, 11, 11)
            .unwrap()
            .and_hms_micro_opt(16, 40, 12, 5)
            .unwrap();
        assert_eq!(
            to_sql_text(&date_range("today", now).0),
            "2026-11-11 00:00:00.000000"
        );
        assert_eq!(
            to_sql_text(&date_range("month", now).0),
            "2026-11-01 00:00:00.000000"
        );
        assert_eq!(
            to_sql_text(&date_range("all", now).0),
            "2000-01-01 00:00:00.000000"
        );
        assert_eq!(date_range("month", now).1, now);
    }

    #[test]
    fn limit_validation() {
        assert_eq!(parse_limit(None).unwrap(), 50);
        assert_eq!(parse_limit(Some("1")).unwrap(), 1);
        assert_eq!(parse_limit(Some("200")).unwrap(), 200);
        assert!(parse_limit(Some("0")).is_err());
        assert!(parse_limit(Some("201")).is_err());
        assert!(parse_limit(Some("abc")).is_err());
        assert!(parse_limit(Some("")).is_err());
    }
}
