//! Chunking parameters and their validation.

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::gear::WINDOW_BYTES;

/// Algorithm identifier written into every manifest.
pub const ALGORITHM_NAME: &str = "gear-cdc";
/// Manifest/algorithm version. Bump on any semantic change.
pub const ALGORITHM_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkParams {
    /// Minimum chunk size in bytes. No boundary is emitted before this many
    /// bytes have accumulated in the current chunk.
    pub min_size: usize,
    /// Target average size is `2^avg_bits` bytes: a boundary is emitted when
    /// `hash & mask == 0` with `mask = (1 << avg_bits) - 1`.
    pub avg_bits: u32,
    /// Maximum chunk size in bytes. Forces a split regardless of the hash.
    pub max_size: usize,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ParamsError {
    #[error("min_size ({min}) must be >= rolling window ({window})")]
    MinBelowWindow { min: usize, window: usize },
    #[error("avg_bits ({bits}) out of supported range 8..=24")]
    AvgBitsOutOfRange { bits: u32 },
    #[error("min_size ({min}) must be < target average (2^{bits})")]
    MinNotBelowAvg { min: usize, bits: u32 },
    #[error("target average (2^{bits}) must be <= max_size ({max})")]
    AvgAboveMax { bits: u32, max: usize },
}

impl ChunkParams {
    pub fn validate(&self) -> Result<(), ParamsError> {
        if self.min_size < WINDOW_BYTES {
            return Err(ParamsError::MinBelowWindow {
                min: self.min_size,
                window: WINDOW_BYTES,
            });
        }
        if !(8..=24).contains(&self.avg_bits) {
            return Err(ParamsError::AvgBitsOutOfRange { bits: self.avg_bits });
        }
        if self.min_size >= (1usize << self.avg_bits) {
            return Err(ParamsError::MinNotBelowAvg {
                min: self.min_size,
                bits: self.avg_bits,
            });
        }
        if (1usize << self.avg_bits) > self.max_size {
            return Err(ParamsError::AvgAboveMax {
                bits: self.avg_bits,
                max: self.max_size,
            });
        }
        Ok(())
    }

    /// Boundary mask: a boundary is emitted when `hash & mask == 0`.
    pub fn mask(&self) -> u64 {
        (1u64 << self.avg_bits) - 1
    }

    /// Service defaults (64 KiB average).
    pub fn default_params() -> Self {
        ChunkParams {
            min_size: 16 * 1024,
            avg_bits: 16,
            max_size: 256 * 1024,
        }
    }
}

/// Algorithm description embedded in every manifest so that a reader can
/// reproduce (or reject) the chunking without out-of-band knowledge.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AlgorithmSpec {
    pub name: String,
    pub version: u32,
    pub min_size: usize,
    pub avg_bits: u32,
    pub max_size: usize,
    pub mask_hex: String,
    pub chunk_digest: String,
}

impl AlgorithmSpec {
    pub fn from_params(params: &ChunkParams) -> Self {
        AlgorithmSpec {
            name: ALGORITHM_NAME.to_string(),
            version: ALGORITHM_VERSION,
            min_size: params.min_size,
            avg_bits: params.avg_bits,
            max_size: params.max_size,
            mask_hex: format!("0x{:016x}", params.mask()),
            chunk_digest: "sha256".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_valid() {
        ChunkParams::default_params().validate().unwrap();
    }

    #[test]
    fn rejects_min_below_window() {
        let p = ChunkParams { min_size: 8, avg_bits: 12, max_size: 1 << 14 };
        assert_eq!(
            p.validate(),
            Err(ParamsError::MinBelowWindow { min: 8, window: WINDOW_BYTES })
        );
    }

    #[test]
    fn rejects_inverted_bounds() {
        let p = ChunkParams { min_size: 1 << 13, avg_bits: 12, max_size: 1 << 14 };
        assert_eq!(
            p.validate(),
            Err(ParamsError::MinNotBelowAvg { min: 1 << 13, bits: 12 })
        );
        let p = ChunkParams { min_size: 1 << 8, avg_bits: 12, max_size: 1 << 11 };
        assert_eq!(
            p.validate(),
            Err(ParamsError::AvgAboveMax { bits: 12, max: 1 << 11 })
        );
    }

    #[test]
    fn mask_matches_avg_bits() {
        let p = ChunkParams { min_size: 64, avg_bits: 12, max_size: 1 << 14 };
        assert_eq!(p.mask(), 0xFFF);
    }
}
