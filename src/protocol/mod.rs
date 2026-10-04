//! Protocol state machines for the two parties.
//!
//! Protocol (DH-PSI, one-and-a-half rounds):
//! 1. A: for each unique element x, P = H_session(x); send Q = a*P.
//! 2. B: for each unique element y, send R = b*H_session(y);
//!    also send back S_i = b*Q_i in the same order.
//! 3. A: compute T_j = a*R_j locally; element i is in the intersection iff
//!    encode(S_i) ∈ { encode(T_j) }.
//!
//! Set semantics: inputs are normalized (sorted, deduplicated) before any
//! cryptographic operation, so duplicates never appear on the wire and the
//! output is a set. The receiver (A) learns the intersection and |B|; B
//! learns |A| and nothing else. See README "Leakage" section.

use std::collections::HashSet;

use curve25519_dalek::ristretto::RistrettoPoint;
use curve25519_dalek::scalar::Scalar;
use zeroize::Zeroize;

use crate::crypto;
use crate::error::PsiError;

/// What input normalization did to a party's raw element list.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DedupeReport {
    pub input_len: usize,
    pub unique_len: usize,
}

impl DedupeReport {
    pub fn duplicates_removed(&self) -> usize {
        self.input_len - self.unique_len
    }
}

/// Sort + deduplicate: set semantics for the whole protocol.
pub fn normalize_set(elements: &[Vec<u8>]) -> (Vec<Vec<u8>>, DedupeReport) {
    let mut normalized = elements.to_vec();
    normalized.sort();
    normalized.dedup();
    let report = DedupeReport {
        input_len: elements.len(),
        unique_len: normalized.len(),
    };
    (normalized, report)
}

/// Party A: initiates, and is the only party that learns the intersection.
pub struct PartyA {
    session_id: [u8; crypto::SESSION_ID_LEN],
    /// Normalized (sorted, unique) elements; index-aligned with `blinded`.
    elements: Vec<Vec<u8>>,
    scalar: Scalar,
    blinded: Vec<RistrettoPoint>,
}

impl PartyA {
    pub fn new(
        session_id: [u8; crypto::SESSION_ID_LEN],
        elements: &[Vec<u8>],
    ) -> (Self, DedupeReport) {
        let (elements, report) = normalize_set(elements);
        let scalar = crypto::generate_blinding_scalar();
        let blinded = elements
            .iter()
            .map(|e| scalar * crypto::hash_to_point(&session_id, e))
            .collect();
        (
            Self {
                session_id,
                elements,
                scalar,
                blinded,
            },
            report,
        )
    }

    pub fn session_id(&self) -> &[u8; crypto::SESSION_ID_LEN] {
        &self.session_id
    }

    pub fn element_count(&self) -> usize {
        self.elements.len()
    }

    /// Message 1: blinded points, in normalized element order.
    pub fn blinded_message(&self) -> Vec<[u8; crypto::POINT_LEN]> {
        self.blinded.iter().map(crypto::encode_point).collect()
    }

    /// Final step. `b_blinded` and `a_doubly` come from B's response and must
    /// already have passed point validation. Returns the intersection as a
    /// sorted, duplicate-free set of the original elements.
    pub fn compute_intersection(
        &self,
        b_blinded: &[RistrettoPoint],
        a_doubly: &[RistrettoPoint],
    ) -> Result<Vec<Vec<u8>>, PsiError> {
        if a_doubly.len() != self.elements.len() {
            return Err(PsiError::CountMismatch {
                expected: self.elements.len(),
                got: a_doubly.len(),
            });
        }
        let targets: HashSet<[u8; crypto::POINT_LEN]> = b_blinded
            .iter()
            .map(|p| crypto::encode_point(&(self.scalar * p)))
            .collect();
        let mut intersection = Vec::new();
        for (i, doubled) in a_doubly.iter().enumerate() {
            if targets.contains(&crypto::encode_point(doubled)) {
                intersection.push(self.elements[i].clone());
            }
        }
        Ok(intersection)
    }
}

impl Drop for PartyA {
    fn drop(&mut self) {
        self.scalar.zeroize();
    }
}

/// Party B's response message.
pub struct BResponse {
    /// b*H(y) for each of B's unique elements (sorted order).
    pub b_blinded: Vec<[u8; crypto::POINT_LEN]>,
    /// b*Q_i, index-aligned with A's submission.
    pub a_doubly: Vec<[u8; crypto::POINT_LEN]>,
    pub dedupe: DedupeReport,
}

/// Party B: stateless after responding; its scalar is zeroized on return.
pub struct PartyB;

impl PartyB {
    pub fn respond(
        session_id: &[u8; crypto::SESSION_ID_LEN],
        elements: &[Vec<u8>],
        a_blinded: &[RistrettoPoint],
    ) -> BResponse {
        let (elements, dedupe) = normalize_set(elements);
        let mut scalar = crypto::generate_blinding_scalar();
        let b_blinded = elements
            .iter()
            .map(|e| crypto::encode_point(&(scalar * crypto::hash_to_point(session_id, e))))
            .collect();
        let a_doubly = a_blinded
            .iter()
            .map(|p| crypto::encode_point(&(scalar * p)))
            .collect();
        scalar.zeroize();
        BResponse {
            b_blinded,
            a_doubly,
            dedupe,
        }
    }
}
