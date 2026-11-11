//! Gateway configuration, read once from environment variables at startup.

use std::path::PathBuf;

/// Runtime configuration for the gateway.
#[derive(Clone, Debug)]
pub struct Config {
    /// Bind host (`GATEWAY_HOST`, default `127.0.0.1`).
    pub host: String,
    /// Bind port (`GATEWAY_PORT`, default `8080`).
    pub port: u16,
    /// Python backend base URL without trailing slash
    /// (`GATEWAY_BACKEND_URL`, default `http://127.0.0.1:8000`).
    pub backend_url: String,
    /// SQLite database file (`DATABASE_PATH`, default `./data/stock_analysis.db`).
    pub database_path: PathBuf,
}

pub const DEFAULT_HOST: &str = "127.0.0.1";
pub const DEFAULT_PORT: u16 = 8080;
pub const DEFAULT_BACKEND_URL: &str = "http://127.0.0.1:8000";
pub const DEFAULT_DATABASE_PATH: &str = "./data/stock_analysis.db";

impl Config {
    /// Build a config from explicit values, normalizing and validating the backend URL.
    pub fn new(
        host: impl Into<String>,
        port: u16,
        backend_url: &str,
        database_path: impl Into<PathBuf>,
    ) -> Result<Self, String> {
        Ok(Self {
            host: host.into(),
            port,
            backend_url: normalize_backend_url(backend_url)?,
            database_path: database_path.into(),
        })
    }

    /// Read the configuration from the process environment.
    pub fn from_env() -> Result<Self, String> {
        let host = env_or("GATEWAY_HOST", DEFAULT_HOST);
        let port_raw = env_or("GATEWAY_PORT", &DEFAULT_PORT.to_string());
        let port: u16 = port_raw
            .trim()
            .parse()
            .map_err(|_| format!("GATEWAY_PORT is not a valid port: {port_raw:?}"))?;
        let backend = env_or("GATEWAY_BACKEND_URL", DEFAULT_BACKEND_URL);
        let db = env_or("DATABASE_PATH", DEFAULT_DATABASE_PATH);
        Self::new(host, port, &backend, db)
    }

    /// `host:port` string used for binding.
    pub fn bind_addr(&self) -> String {
        if self.host.contains(':') && !self.host.starts_with('[') {
            format!("[{}]:{}", self.host, self.port)
        } else {
            format!("{}:{}", self.host, self.port)
        }
    }
}

fn env_or(name: &str, default: &str) -> String {
    match std::env::var(name) {
        Ok(v) if !v.trim().is_empty() => v,
        _ => default.to_string(),
    }
}

fn normalize_backend_url(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim().trim_end_matches('/');
    let url = reqwest::Url::parse(trimmed)
        .map_err(|e| format!("GATEWAY_BACKEND_URL is not a valid URL ({raw:?}): {e}"))?;
    if url.scheme() != "http" && url.scheme() != "https" {
        return Err(format!("GATEWAY_BACKEND_URL must use http or https: {raw:?}"));
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(format!(
            "GATEWAY_BACKEND_URL must not contain a query or fragment: {raw:?}"
        ));
    }
    Ok(trimmed.to_string())
}
