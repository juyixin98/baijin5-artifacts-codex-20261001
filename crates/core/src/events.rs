//! Structured engine events attached to a request identity.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventLevel {
    Info,
    Warning,
    Error,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineEvent {
    pub step: u32,
    pub module: String,
    pub level: EventLevel,
    pub request_id: String,
    pub message: String,
}

/// Collects ordered, human-auditable processing events.
#[derive(Debug, Clone, Default)]
pub struct EventLog {
    pub request_id: String,
    pub events: Vec<EngineEvent>,
}

impl EventLog {
    pub fn new(request_id: impl Into<String>) -> Self {
        EventLog {
            request_id: request_id.into(),
            events: Vec::new(),
        }
    }

    pub fn record(&mut self, module: impl Into<String>, message: impl Into<String>) {
        let event = EngineEvent {
            step: self.events.len() as u32 + 1,
            module: module.into(),
            level: EventLevel::Info,
            request_id: self.request_id.clone(),
            message: message.into(),
        };
        self.events.push(event);
    }

    pub fn warn(&mut self, module: impl Into<String>, message: impl Into<String>) {
        let mut event = EngineEvent {
            step: self.events.len() as u32 + 1,
            module: module.into(),
            level: EventLevel::Warning,
            request_id: self.request_id.clone(),
            message: message.into(),
        };
        event.level = EventLevel::Warning;
        self.events.push(event);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stamps_every_event_with_request_id() {
        let mut log = EventLog::new("req-7");
        log.record("cnf", "encoded side A");
        log.warn("budget", "cap reached");
        assert_eq!(log.events.len(), 2);
        assert!(log.events.iter().all(|event| event.request_id == "req-7"));
        assert_eq!(log.events[1].level, EventLevel::Warning);
        assert_eq!(log.events[1].step, 2);
    }
}
