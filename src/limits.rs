//! Resource control: hard bounds on what the service will accept and how
//! much work it will do concurrently.

use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Limits {
    /// Maximum request body in bytes (applies to chunk and verify inputs).
    pub max_body_bytes: usize,
    /// Maximum number of chunks a single manifest may contain.
    pub max_chunks: usize,
    /// Maximum concurrently processed requests; excess requests wait.
    pub max_concurrent: usize,
    /// Per-request processing timeout.
    #[serde(with = "humantime_secs")]
    pub request_timeout: Duration,
}

impl Default for Limits {
    fn default() -> Self {
        Limits {
            max_body_bytes: 64 * 1024 * 1024,
            max_chunks: 65_536,
            max_concurrent: 64,
            request_timeout: Duration::from_secs(30),
        }
    }
}

/// Serde helper: serialize a `Duration` as whole seconds in config files.
mod humantime_secs {
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::time::Duration;

    pub fn serialize<S: Serializer>(d: &Duration, s: S) -> Result<S::Ok, S::Error> {
        d.as_secs().serialize(s)
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<Duration, D::Error> {
        Ok(Duration::from_secs(u64::deserialize(d)?))
    }
}

/// Why a request was rejected by resource control. Stable categories, used
/// in API error bodies and diagnostics.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LimitRejection {
    BodyTooLarge,
    TooManyChunks,
    Timeout,
}

impl LimitRejection {
    pub fn category(&self) -> &'static str {
        match self {
            LimitRejection::BodyTooLarge => "body_too_large",
            LimitRejection::TooManyChunks => "too_many_chunks",
            LimitRejection::Timeout => "timeout",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_sane() {
        let l = Limits::default();
        assert!(l.max_body_bytes > 0);
        assert!(l.max_chunks > 0);
        assert!(l.max_concurrent > 0);
        assert!(l.request_timeout > Duration::ZERO);
    }

    #[test]
    fn categories_are_stable() {
        assert_eq!(LimitRejection::BodyTooLarge.category(), "body_too_large");
        assert_eq!(LimitRejection::TooManyChunks.category(), "too_many_chunks");
        assert_eq!(LimitRejection::Timeout.category(), "timeout");
    }
}
