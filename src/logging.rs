//! Structured, request-correlated logging.
//!
//! Every emitted line is a single JSON object carrying the request id, a
//! monotonic millisecond offset since the check started, the processing
//! location (stream line + logical phase), the event and an explanatory
//! message. Logs go to stderr so they never corrupt the stdout verdict report.

use std::io::Write;
use std::time::Instant;

/// Where in the stream an event happened.
#[derive(Debug, Clone, Copy)]
pub struct Location {
    pub line: u64,
}

pub struct Logger {
    request_id: String,
    enabled: bool,
    start: Instant,
}

impl Logger {
    pub fn new(request_id: impl Into<String>, enabled: bool) -> Self {
        Logger {
            request_id: request_id.into(),
            enabled,
            start: Instant::now(),
        }
    }

    pub fn request_id(&self) -> &str {
        &self.request_id
    }

    fn emit(&self, level: &str, event: &str, message: &str, line: Option<u64>) {
        if !self.enabled {
            return;
        }
        let elapsed_ms = self.start.elapsed().as_millis() as u64;
        let mut obj = serde_json::Map::new();
        obj.insert("ts_ms".into(), elapsed_ms.into());
        obj.insert("level".into(), level.into());
        obj.insert("request_id".into(), self.request_id.clone().into());
        obj.insert("event".into(), event.into());
        obj.insert("message".into(), message.into());
        if let Some(l) = line {
            obj.insert("line".into(), l.into());
        }
        let line_text = serde_json::Value::Object(obj).to_string();
        let mut err = std::io::stderr().lock();
        let _ = writeln!(err, "{line_text}");
    }

    pub fn info(&self, event: &str, message: impl std::fmt::Display) {
        self.emit("info", event, &message.to_string(), None);
    }

    pub fn info_at(&self, loc: Location, event: &str, message: impl std::fmt::Display) {
        self.emit("info", event, &message.to_string(), Some(loc.line));
    }

    pub fn warn(&self, event: &str, message: impl std::fmt::Display) {
        self.emit("warn", event, &message.to_string(), None);
    }

    pub fn warn_at(&self, loc: Location, event: &str, message: impl std::fmt::Display) {
        self.emit("warn", event, &message.to_string(), Some(loc.line));
    }

    pub fn error_at(&self, loc: Location, event: &str, message: impl std::fmt::Display) {
        self.emit("error", event, &message.to_string(), Some(loc.line));
    }
}
