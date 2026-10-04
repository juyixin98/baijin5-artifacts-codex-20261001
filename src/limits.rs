//! Resource control: hard caps enforced before and during chunking so a
//! single request cannot exhaust memory or CPU.

use crate::error::{CdcError, ErrorCategory};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Limits {
    /// Maximum input payload bytes accepted for chunking.
    pub max_input_bytes: usize,
    /// Maximum HTTP request body bytes (applies to container uploads too).
    pub max_body_bytes: usize,
    /// Maximum number of chunks a single input may produce.
    pub max_chunks: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_input_bytes: 64 * 1024 * 1024,
            max_body_bytes: 80 * 1024 * 1024,
            max_chunks: 1_000_000,
        }
    }
}

impl Limits {
    pub fn check_input(&self, len: usize) -> Result<(), CdcError> {
        if len > self.max_input_bytes {
            return Err(CdcError::new(
                ErrorCategory::LimitExceeded,
                format!(
                    "input of {len} bytes exceeds max_input_bytes {}",
                    self.max_input_bytes
                ),
            ));
        }
        Ok(())
    }

    pub fn check_body(&self, len: usize) -> Result<(), CdcError> {
        if len > self.max_body_bytes {
            return Err(CdcError::new(
                ErrorCategory::LimitExceeded,
                format!(
                    "request body of {len} bytes exceeds max_body_bytes {}",
                    self.max_body_bytes
                ),
            ));
        }
        Ok(())
    }

    pub fn check_chunk_count(&self, count: usize) -> Result<(), CdcError> {
        if count > self.max_chunks {
            return Err(CdcError::new(
                ErrorCategory::LimitExceeded,
                format!("chunk count {count} exceeds max_chunks {}", self.max_chunks),
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn limit_violations_are_categorized() {
        let limits = Limits { max_input_bytes: 10, max_body_bytes: 20, max_chunks: 3 };
        assert!(limits.check_input(10).is_ok());
        assert_eq!(limits.check_input(11).unwrap_err().category, ErrorCategory::LimitExceeded);
        assert_eq!(limits.check_body(21).unwrap_err().category, ErrorCategory::LimitExceeded);
        assert_eq!(limits.check_chunk_count(4).unwrap_err().category, ErrorCategory::LimitExceeded);
    }
}
