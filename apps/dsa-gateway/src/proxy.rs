//! Transparent streaming reverse proxy to the Python backend.

use std::net::SocketAddr;

use axum::body::{Body, HttpBody};
use axum::extract::{ConnectInfo, Request, State};
use axum::http::header::{self, HeaderMap, HeaderName, HeaderValue};
use axum::response::Response;

use crate::errors::bad_gateway;
use crate::AppState;

/// Hop-by-hop headers (RFC 9110 §7.6.1) that must not be forwarded.
const HOP_BY_HOP: [&str; 8] = [
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
];

fn is_hop_by_hop(name: &HeaderName) -> bool {
    HOP_BY_HOP.contains(&name.as_str())
}

/// Header names listed in `Connection` are hop-by-hop for this message too.
fn connection_listed(headers: &HeaderMap) -> Vec<String> {
    headers
        .get_all(header::CONNECTION)
        .iter()
        .filter_map(|v| v.to_str().ok())
        .flat_map(|v| v.split(','))
        .map(|s| s.trim().to_ascii_lowercase())
        .filter(|s| !s.is_empty())
        .collect()
}

/// Copy headers, dropping hop-by-hop ones and anything in `skip`; repeated
/// headers (e.g. several `Set-Cookie`) are preserved.
fn filter_headers(src: &HeaderMap, skip: &[HeaderName]) -> HeaderMap {
    let listed = connection_listed(src);
    let mut out = HeaderMap::with_capacity(src.len());
    for (name, value) in src.iter() {
        if is_hop_by_hop(name)
            || skip.contains(name)
            || listed.iter().any(|l| l == name.as_str())
        {
            continue;
        }
        out.append(name.clone(), value.clone());
    }
    out
}

pub async fn proxy(State(state): State<AppState>, req: Request) -> Response {
    let client_ip = req
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|ci| ci.0.ip());
    let (parts, body) = req.into_parts();

    let path_and_query = parts
        .uri
        .path_and_query()
        .map(|pq| pq.as_str())
        .unwrap_or("/");
    let url = format!("{}{}", state.config.backend_url, path_and_query);

    // reqwest/hyper derive Host from the URL and Content-Length from the body.
    let mut headers = filter_headers(&parts.headers, &[header::HOST, header::CONTENT_LENGTH]);

    if let Some(ip) = client_ip {
        let xff = match parts
            .headers
            .get("x-forwarded-for")
            .and_then(|v| v.to_str().ok())
        {
            Some(prev) if !prev.trim().is_empty() => format!("{prev}, {ip}"),
            _ => ip.to_string(),
        };
        if let Ok(v) = HeaderValue::from_str(&xff) {
            headers.insert("x-forwarded-for", v);
        }
    }
    if !headers.contains_key("x-forwarded-host") {
        if let Some(host) = parts.headers.get(header::HOST) {
            headers.insert("x-forwarded-host", host.clone());
        }
    }
    if !headers.contains_key("x-forwarded-proto") {
        headers.insert("x-forwarded-proto", HeaderValue::from_static("http"));
    }

    let mut upstream = state
        .client
        .request(parts.method.clone(), &url)
        .headers(headers);

    // Stream the request body. When the incoming body length is known exactly we
    // keep it so the backend gets a Content-Length instead of chunked encoding
    // (the body bytes are forwarded unchanged, so the length stays correct).
    match body.size_hint().exact() {
        Some(0) => {}
        Some(len) => {
            upstream = upstream
                .header(header::CONTENT_LENGTH, len)
                .body(reqwest::Body::wrap_stream(body.into_data_stream()));
        }
        None => {
            upstream = upstream.body(reqwest::Body::wrap_stream(body.into_data_stream()));
        }
    }

    let resp = match upstream.send().await {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[dsa-gateway] proxy {} {} failed: {e}", parts.method, path_and_query);
            return bad_gateway();
        }
    };

    let status = resp.status();
    let resp_headers = filter_headers(resp.headers(), &[]);
    let mut out = Response::new(Body::from_stream(resp.bytes_stream()));
    *out.status_mut() = status;
    *out.headers_mut() = resp_headers;
    out
}
