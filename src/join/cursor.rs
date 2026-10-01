//! Opaque continuation tokens for resumable pagination.
//!
//! A token records the last fully emitted tuple in *canonical* attribute order
//! and a format version. It encodes an exclusive lower bound: resuming skips
//! the encoded tuple and everything lexicographically before it.
use base64::Engine;
use serde::{Deserialize, Serialize};

use crate::domain::{Datum, LogicalType};
use crate::error::{ErrorCode, JoinError, JoinResult};

const VERSION: u32 = 1;

#[derive(Debug, Serialize, Deserialize)]
struct Token {
    v: u32,
    t: Vec<Datum>,
}

/// Encode the last emitted canonical tuple.
pub fn encode_after(tuple: &[Datum]) -> String {
    let token = Token {
        v: VERSION,
        t: tuple.to_vec(),
    };
    let json = serde_json::to_vec(&token).expect("token serialization is infallible");
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(json)
}

/// Decode a caller-supplied token and validate it against the canonical output
/// schema (arity and per-attribute types).
pub fn decode_after(token: &str, types: &[LogicalType]) -> JoinResult<Vec<Datum>> {
    let raw = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(token.as_bytes())
        .map_err(|_| JoinError::new(ErrorCode::InvalidCursor, "cursor is not valid base64"))?;
    let parsed: Token = serde_json::from_slice(&raw).map_err(|_| {
        JoinError::new(
            ErrorCode::InvalidCursor,
            "cursor payload is not a valid token",
        )
    })?;
    if parsed.v != VERSION {
        return Err(JoinError::new(
            ErrorCode::InvalidCursor,
            format!("unsupported cursor version {}", parsed.v),
        ));
    }
    if parsed.t.len() != types.len() {
        return Err(JoinError::new(
            ErrorCode::InvalidCursor,
            format!(
                "cursor arity {} does not match output arity {}",
                parsed.t.len(),
                types.len()
            ),
        ));
    }
    for (i, (datum, ty)) in parsed.t.iter().zip(types).enumerate() {
        if let Some(actual) = datum.logical_type() {
            if &actual != ty {
                return Err(JoinError::new(
                    ErrorCode::InvalidCursor,
                    format!(
                        "cursor position {i} has type {} but output column is {}",
                        actual.as_str(),
                        ty.as_str()
                    ),
                ));
            }
        }
    }
    Ok(parsed.t)
}
