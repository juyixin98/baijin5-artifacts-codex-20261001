//! Error taxonomy for the CoW snapshot service.
//!
//! Every failure surfaced by the service carries a stable [`ErrorCategory`] so
//! that operators and tests can distinguish:
//!
//! | Category           | Meaning                                             | HTTP |
//! |--------------------|-----------------------------------------------------|------|
//! | `Input`            | malformed request (bad page, offset, base64, ...)   | 400  |
//! | `StateConflict`    | valid request conflicting with current state        | 409  |
//! | `ResourceExhausted`| page-object capacity exhausted                      | 507  |
//! | `Integrity`        | refcount / metadata anomaly; service quarantines    | 500  |
//! | `Internal`         | unexpected IO / internal failure                    | 500  |

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    Input,
    StateConflict,
    ResourceExhausted,
    Integrity,
    Internal,
}

#[derive(Debug, Clone, Serialize)]
pub struct ServiceError {
    pub category: ErrorCategory,
    /// Stable machine-readable code, e.g. `capacity_exhausted`.
    pub code: &'static str,
    pub message: String,
}

impl ServiceError {
    pub fn input(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            category: ErrorCategory::Input,
            code,
            message: message.into(),
        }
    }
    pub fn conflict(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            category: ErrorCategory::StateConflict,
            code,
            message: message.into(),
        }
    }
    pub fn exhausted(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            category: ErrorCategory::ResourceExhausted,
            code,
            message: message.into(),
        }
    }
    pub fn integrity(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            category: ErrorCategory::Integrity,
            code,
            message: message.into(),
        }
    }
    pub fn internal(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            category: ErrorCategory::Internal,
            code,
            message: message.into(),
        }
    }
}

impl std::fmt::Display for ServiceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}/{}: {}", self.category, self.code, self.message)
    }
}

impl std::error::Error for ServiceError {}

impl From<std::io::Error> for ServiceError {
    fn from(e: std::io::Error) -> Self {
        Self::internal("io", format!("io error: {e}"))
    }
}

pub type Result<T> = std::result::Result<T, ServiceError>;
