//! Error taxonomy shared by every module.
//!
//! The four categories are deliberately coarse so that API consumers and
//! tests can distinguish failure classes without parsing message text:
//!
//! | category            | meaning                                   | HTTP |
//! |---------------------|-------------------------------------------|------|
//! | `input`             | malformed / semantically invalid request  | 400 (404 for `run_not_found`) |
//! | `state_conflict`    | request conflicts with run lifecycle      | 409  |
//! | `resource_exhausted`| a configured limit was exceeded           | 413  |
//! | `computation`       | internal computation / persistence failure| 500  |

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    Input,
    StateConflict,
    ResourceExhausted,
    Computation,
}

#[derive(Debug, Clone, Serialize)]
pub struct BackendError {
    pub category: ErrorCategory,
    /// Stable machine-readable code, e.g. `run_sealed`, `async_cycle`.
    pub code: String,
    /// Human-readable detail, safe to surface to callers.
    pub message: String,
}

impl BackendError {
    pub fn input(code: &str, message: impl Into<String>) -> Self {
        Self { category: ErrorCategory::Input, code: code.to_string(), message: message.into() }
    }

    pub fn conflict(code: &str, message: impl Into<String>) -> Self {
        Self { category: ErrorCategory::StateConflict, code: code.to_string(), message: message.into() }
    }

    pub fn resource(code: &str, message: impl Into<String>) -> Self {
        Self { category: ErrorCategory::ResourceExhausted, code: code.to_string(), message: message.into() }
    }

    pub fn computation(code: &str, message: impl Into<String>) -> Self {
        Self { category: ErrorCategory::Computation, code: code.to_string(), message: message.into() }
    }

    /// HTTP status this error maps to at the API boundary.
    pub fn status(&self) -> u16 {
        match self.category {
            ErrorCategory::Input => {
                if self.code == "run_not_found" {
                    404
                } else {
                    400
                }
            }
            ErrorCategory::StateConflict => 409,
            ErrorCategory::ResourceExhausted => 413,
            ErrorCategory::Computation => 500,
        }
    }
}

impl std::fmt::Display for BackendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}/{}: {}", self.category, self.code, self.message)
    }
}

impl std::error::Error for BackendError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn categories_map_to_distinct_statuses() {
        assert_eq!(BackendError::input("bad_weight", "x").status(), 400);
        assert_eq!(BackendError::input("run_not_found", "x").status(), 404);
        assert_eq!(BackendError::conflict("run_sealed", "x").status(), 409);
        assert_eq!(BackendError::resource("sample_limit", "x").status(), 413);
        assert_eq!(BackendError::computation("weight_overflow", "x").status(), 500);
    }
}
