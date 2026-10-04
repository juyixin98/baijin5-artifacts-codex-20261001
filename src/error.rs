//! Error taxonomy for the codec. Every failure carries enough location
//! information (block index, byte offset) to pinpoint a corrupted header
//! without the decoder ever reading out of bounds.

use thiserror::Error;

/// Failure categories, stable for API consumers and test assertions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCategory {
    BadMagic,
    TruncatedHeader,
    InvalidMode,
    InvalidBitWidth,
    InvalidReserved,
    TruncatedBody,
    BudgetExceeded,
    EmptyInput,
}

/// A codec error with a machine-readable category and human-readable detail.
#[derive(Debug, Error)]
#[error("{category:?}: {message}")]
pub struct CodecError {
    pub category: ErrorCategory,
    pub message: String,
    /// 0-based index of the block being processed when the error occurred.
    pub block_index: Option<usize>,
    /// Absolute byte offset in the stream, when known.
    pub offset: Option<usize>,
}

impl CodecError {
    pub fn new(category: ErrorCategory, message: impl Into<String>) -> Self {
        CodecError {
            category,
            message: message.into(),
            block_index: None,
            offset: None,
        }
    }

    pub fn at(mut self, block_index: usize, offset: usize) -> Self {
        self.block_index = Some(block_index);
        self.offset = Some(offset);
        self
    }

    pub fn bad_magic(found: [u8; 4]) -> Self {
        Self::new(
            ErrorCategory::BadMagic,
            format!("bad stream magic {found:02x?}, expected RBP1"),
        )
        .at(0, 0)
    }

    pub fn truncated_header(block: usize, offset: usize, have: usize) -> Self {
        Self::new(
            ErrorCategory::TruncatedHeader,
            format!("block header needs 8 bytes, only {have} remain"),
        )
        .at(block, offset)
    }

    pub fn invalid_mode(block: usize, offset: usize, tag: u8) -> Self {
        Self::new(
            ErrorCategory::InvalidMode,
            format!("unknown mode tag {tag} (legal: 0=RLE, 1=BITPACK)"),
        )
        .at(block, offset)
    }

    pub fn invalid_bit_width(block: usize, offset: usize, width: u8) -> Self {
        Self::new(
            ErrorCategory::InvalidBitWidth,
            format!("bit_width {width} exceeds maximum 64"),
        )
        .at(block, offset)
    }

    pub fn invalid_reserved(block: usize, offset: usize, value: u16) -> Self {
        Self::new(
            ErrorCategory::InvalidReserved,
            format!("reserved header field must be 0, found {value}"),
        )
        .at(block, offset)
    }

    pub fn truncated_body(
        block: usize,
        offset: usize,
        need: usize,
        have: usize,
    ) -> Self {
        Self::new(
            ErrorCategory::TruncatedBody,
            format!("declared body needs {need} bytes, only {have} remain"),
        )
        .at(block, offset)
    }

    pub fn budget_exceeded(kind: &str, limit: usize, requested: usize) -> Self {
        Self::new(
            ErrorCategory::BudgetExceeded,
            format!("{kind} budget exceeded: limit {limit}, requested {requested}"),
        )
    }
}
