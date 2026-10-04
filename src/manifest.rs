//! Chunking manifest: the algorithm identity and full parameter set that
//! produced a chunk index. Written into every container header and returned
//! by the API, so a consumer can detect parameter drift instead of silently
//! trusting boundaries.

use crate::chunker::{ChunkParams, ALGORITHM_ID, WINDOW_BYTES};
use crate::gear::GEAR_SEED;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Manifest {
    pub algorithm: String,
    pub window_bytes: usize,
    pub gear_seed: String,
    pub digest: String,
    pub params: ChunkParams,
}

impl Manifest {
    pub fn for_params(params: ChunkParams) -> Self {
        Self {
            algorithm: ALGORITHM_ID.to_string(),
            window_bytes: WINDOW_BYTES,
            gear_seed: format!("{GEAR_SEED:#018x}"),
            digest: "sha256".to_string(),
            params,
        }
    }

    /// True when `other` describes the identical chunking regime.
    pub fn compatible_with(&self, other: &Manifest) -> bool {
        self == other
    }

    /// Canonical JSON bytes (sorted keys via struct order) used for the
    /// manifest digest.
    pub fn canonical_json(&self) -> Vec<u8> {
        // serde_json serializes struct fields in declaration order — stable
        // across runs for this type.
        serde_json::to_vec(self).expect("manifest serialization is infallible")
    }

    /// sha256 of the canonical JSON, hex-encoded. Identifies the regime,
    /// not the data.
    pub fn digest_hex(&self) -> String {
        hex::encode(Sha256::digest(&self.canonical_json()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_digest_is_stable_and_param_sensitive() {
        let m1 = Manifest::for_params(ChunkParams::default());
        let m2 = Manifest::for_params(ChunkParams::default());
        assert_eq!(m1.digest_hex(), m2.digest_hex());

        let mut altered = ChunkParams::default();
        altered.mask_bits += 1;
        let m3 = Manifest::for_params(altered);
        assert_ne!(m1.digest_hex(), m3.digest_hex());
        assert!(!m1.compatible_with(&m3));
    }
}
