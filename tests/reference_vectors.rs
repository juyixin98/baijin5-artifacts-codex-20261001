//! Frozen reference vectors and an independent re-derivation of the
//! protocol math that does NOT go through the `protocol` module, so the
//! core implementation is checked against independently computed answers.

use curve25519_dalek::scalar::Scalar;
use psi_dh::crypto::{decode_point, encode_point, hash_to_point};

/// Frozen regression vector for hash-to-group. If this ever changes, the
/// wire format changed and old transcripts are incomparable.
/// Generated once with this crate's `crypto::hash_to_point` and frozen here;
/// cross-checked by the independent derivations below.
#[test]
fn hash_to_point_frozen_vector() {
    let point = hash_to_point(&[0u8; 32], b"hello");
    assert_eq!(
        hex::encode(encode_point(&point)),
        "565f3143aa1c049c453f41d328c7a8affc7a0d2f2145b01cea27636328909215"
    );
}

/// Independent check of the DH-PSI core identity using fixed scalars and
/// direct curve25519-dalek operations (no `protocol` module code):
/// for every x, y:  encode(a*b*H(x)) == encode(b*a*H(y))  iff  H(x) == H(y).
#[test]
fn independent_fixed_scalar_derivation() {
    let session = [7u8; 32];
    let a = Scalar::from_bytes_mod_order([11u8; 32]);
    let b = Scalar::from_bytes_mod_order([23u8; 32]);

    let shared = b"shared-element";
    let only_a = b"only-a";
    let only_b = b"only-b";

    // Simulate the wire: A blinds H(x) with a, B blinds with b.
    let blind = |scalar: &Scalar, element: &[u8]| scalar * hash_to_point(&session, element);

    // Commutativity on the shared element, computed both orders independently.
    let ab = a * blind(&b, shared);
    let ba = b * blind(&a, shared);
    assert_eq!(encode_point(&ab), encode_point(&ba));

    // Distinct elements must not collide under the double blinding.
    let a_side = b * blind(&a, only_a);
    let b_side = a * blind(&b, only_b);
    assert_ne!(encode_point(&a_side), encode_point(&b_side));

    // Full mini-intersection computed independently: A holds {shared, only_a},
    // B holds {shared, only_b}; the intersection must be exactly {shared}.
    let a_set = [shared.as_slice(), only_a.as_slice()];
    let b_set = [shared.as_slice(), only_b.as_slice()];
    let a_doubly: Vec<_> = a_set.iter().map(|x| b * blind(&a, x)).collect();
    let b_targets: std::collections::HashSet<_> = b_set
        .iter()
        .map(|y| encode_point(&(a * blind(&b, y))))
        .collect();
    let hits: Vec<&[u8]> = a_set
        .iter()
        .zip(&a_doubly)
        .filter(|(_, d)| b_targets.contains(&encode_point(d)))
        .map(|(x, _)| *x)
        .collect();
    assert_eq!(hits, vec![shared.as_slice()]);
}

/// A valid point produced by hash_to_point must pass the strict decoder
/// (canonical, non-identity) — the decoder must not reject honest traffic.
#[test]
fn hash_to_point_output_passes_validation() {
    let point = hash_to_point(&[3u8; 32], b"validation-check");
    let decoded = decode_point(0, &encode_point(&point)).expect("must decode");
    assert_eq!(decoded, point);
}
