//! Structured error categories. Every decode failure carries the byte offset
//! at which the problem was located, so corrupted blocks are *located*, never
//! read out of bounds.

use std::fmt;

/// Error category, stable across releases; used by tests and the HTTP API.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCategory {
    TruncatedHeader,
    BadMagic,
    UnsupportedVersion,
    UnknownMode,
    InvalidBitWidth,
    NonZeroReserved,
    ValueCountOverBudget,
    PayloadLenOverBudget,
    PayloadLenMismatch,
    TruncatedPayload,
    RunLengthZero,
    RunLengthSumMismatch,
    RunCountOverBudget,
    TrailingBytes,
    EmptyInput,
}

impl ErrorCategory {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCategory::TruncatedHeader => "truncated_header",
            ErrorCategory::BadMagic => "bad_magic",
            ErrorCategory::UnsupportedVersion => "unsupported_version",
            ErrorCategory::UnknownMode => "unknown_mode",
            ErrorCategory::InvalidBitWidth => "invalid_bit_width",
            ErrorCategory::NonZeroReserved => "non_zero_reserved",
            ErrorCategory::ValueCountOverBudget => "value_count_over_budget",
            ErrorCategory::PayloadLenOverBudget => "payload_len_over_budget",
            ErrorCategory::PayloadLenMismatch => "payload_len_mismatch",
            ErrorCategory::TruncatedPayload => "truncated_payload",
            ErrorCategory::RunLengthZero => "run_length_zero",
            ErrorCategory::RunLengthSumMismatch => "run_length_sum_mismatch",
            ErrorCategory::RunCountOverBudget => "run_count_over_budget",
            ErrorCategory::TrailingBytes => "trailing_bytes",
            ErrorCategory::EmptyInput => "empty_input",
        }
    }
}

/// A codec error with the byte offset (within the input buffer given to the
/// decoder) at which it was detected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub category: ErrorCategory,
    pub offset: usize,
    pub detail: String,
}

impl Error {
    pub fn new(category: ErrorCategory, offset: usize, detail: impl Into<String>) -> Self {
        Error {
            category,
            offset,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} at offset {}: {}",
            self.category.as_str(),
            self.offset,
            self.detail
        )
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;
