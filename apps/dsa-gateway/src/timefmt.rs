//! Datetime helpers that mirror Python's `datetime.isoformat()` and the
//! SQLAlchemy SQLite storage format.

use chrono::{NaiveDate, NaiveDateTime, Timelike};

/// Format SQLAlchemy uses for `DATETIME` columns on SQLite.
pub const SQL_FORMAT: &str = "%Y-%m-%d %H:%M:%S%.6f";

/// Render a naive datetime for binding against SQLAlchemy-stored text.
pub fn to_sql_text(dt: &NaiveDateTime) -> String {
    dt.format(SQL_FORMAT).to_string()
}

/// Python `datetime.isoformat()`: fractional part only when microseconds != 0.
pub fn python_isoformat(dt: &NaiveDateTime) -> String {
    if dt.nanosecond() / 1_000 == 0 {
        dt.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        dt.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
    }
}

/// Convert a stored `called_at` text value to Python's isoformat; unparsable
/// values are returned unchanged.
pub fn stored_to_isoformat(raw: &str) -> String {
    let parsed = NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S"))
        .or_else(|_| {
            NaiveDate::parse_from_str(raw, "%Y-%m-%d")
                .map(|d| d.and_hms_opt(0, 0, 0).expect("midnight is valid"))
        });
    match parsed {
        Ok(dt) => python_isoformat(&dt),
        Err(_) => raw.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isoformat_matches_python() {
        assert_eq!(
            stored_to_isoformat("2026-01-02 03:04:05.000000"),
            "2026-01-02T03:04:05"
        );
        assert_eq!(
            stored_to_isoformat("2026-01-02 03:04:05.123456"),
            "2026-01-02T03:04:05.123456"
        );
        assert_eq!(
            stored_to_isoformat("2026-01-02 03:04:05.000010"),
            "2026-01-02T03:04:05.000010"
        );
        assert_eq!(stored_to_isoformat("2026-01-02 03:04:05"), "2026-01-02T03:04:05");
        assert_eq!(stored_to_isoformat("2026-01-02"), "2026-01-02T00:00:00");
        assert_eq!(stored_to_isoformat("not a date"), "not a date");
    }

    #[test]
    fn sql_text_has_six_fraction_digits() {
        let dt = NaiveDate::from_ymd_opt(2026, 1, 2)
            .unwrap()
            .and_hms_opt(3, 4, 5)
            .unwrap();
        assert_eq!(to_sql_text(&dt), "2026-01-02 03:04:05.000000");
    }
}
