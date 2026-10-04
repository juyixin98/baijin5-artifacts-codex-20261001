//! Cryptographic adapter: the only module that talks to curve25519-dalek.
//!
//! Choices (see README for the full rationale):
//! - Group: ristretto255 (prime-order, canonical encodings) via
//!   `curve25519-dalek`, a mature, audited implementation.
//! - Hash-to-group: SHA-512 with domain separation, fed to
//!   `RistrettoPoint::from_uniform_bytes` (the crate's Elligator2-based map).
//!   The per-session random id is mixed in, so the same element maps to a
//!   different point in every session (session-domain isolation).
//! - Point validation: every incoming point must be 32 bytes, must
//!   decompress (canonical encoding, on-curve by ristretto construction), and
//!   must not be the identity. Validation is NOT optional.

use curve25519_dalek::ristretto::{CompressedRistretto, RistrettoPoint};
use curve25519_dalek::scalar::Scalar;
use curve25519_dalek::traits::Identity;
use rand_core::{OsRng, RngCore};
use sha2::{Digest, Sha512};

use crate::error::PsiError;

pub const POINT_LEN: usize = 32;
pub const SESSION_ID_LEN: usize = 32;

/// Domain separation tag for hash-to-group. Versioned so a future protocol
/// change cannot collide with transcripts produced by this version.
pub const HASH_TO_GROUP_DST: &[u8] = b"PSI-DH/RISTRETTO255/SHA512-ELL2/v1";

/// Fresh 256-bit session id from the OS CSPRNG. Generated once per session;
/// never reused across sessions.
pub fn generate_session_id() -> [u8; SESSION_ID_LEN] {
    let mut id = [0u8; SESSION_ID_LEN];
    OsRng.fill_bytes(&mut id);
    id
}

/// Fresh blinding scalar from the OS CSPRNG. Each party generates a new one
/// per session; it never leaves the party's process.
pub fn generate_blinding_scalar() -> Scalar {
    Scalar::random(&mut OsRng)
}

/// Map an element to a group element, bound to this session.
///
/// `SHA-512(DST || 0x00 || session_id || LE64(len(element)) || element)`
/// gives 64 uniform bytes for `from_uniform_bytes`. Length-prefixing the
/// element removes concatenation ambiguity.
pub fn hash_to_point(session_id: &[u8; SESSION_ID_LEN], element: &[u8]) -> RistrettoPoint {
    let mut hasher = Sha512::new();
    hasher.update(HASH_TO_GROUP_DST);
    hasher.update([0x00u8]);
    hasher.update(session_id);
    hasher.update((element.len() as u64).to_le_bytes());
    hasher.update(element);
    let uniform: [u8; 64] = hasher.finalize().into();
    RistrettoPoint::from_uniform_bytes(&uniform)
}

/// Canonical 32-byte encoding of a point.
pub fn encode_point(point: &RistrettoPoint) -> [u8; POINT_LEN] {
    point.compress().to_bytes()
}

/// Decode and fully validate one point. `index` is the point's position in
/// the enclosing message, used only for diagnostics.
pub fn decode_point(index: usize, bytes: &[u8]) -> Result<RistrettoPoint, PsiError> {
    if bytes.len() != POINT_LEN {
        return Err(PsiError::BadPointLength {
            index,
            got: bytes.len(),
        });
    }
    let mut arr = [0u8; POINT_LEN];
    arr.copy_from_slice(bytes);
    let point = CompressedRistretto(arr)
        .decompress()
        .ok_or(PsiError::InvalidPointEncoding { index })?;
    // The identity would make every blinded value equal, collapsing the
    // protocol and leaking structure; reject it outright.
    if point == RistrettoPoint::identity() {
        return Err(PsiError::IdentityPoint { index });
    }
    Ok(point)
}

/// Decode a whole message worth of points, failing on the first bad one.
pub fn decode_points(encoded: &[[u8; POINT_LEN]]) -> Result<Vec<RistrettoPoint>, PsiError> {
    encoded
        .iter()
        .enumerate()
        .map(|(i, p)| decode_point(i, p))
        .collect()
}
