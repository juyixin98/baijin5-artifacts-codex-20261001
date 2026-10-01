//! FNV-1a 64-bit — a tiny, dependency-free, fully deterministic hash.
//!
//! Determinism (same output on every platform/build) is the property the grouping
//! contract needs; cryptographic strength is not required for bucket placement.

const OFFSET_BASIS: u64 = 0xcbf2_9ce4_8422_2325;
const PRIME: u64 = 0x0000_0100_0000_01b3;

pub fn fnv1a_64(bytes: &[u8]) -> u64 {
    let mut hash = OFFSET_BASIS;
    for &b in bytes {
        hash ^= b as u64;
        hash = hash.wrapping_mul(PRIME);
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::fnv1a_64;

    #[test]
    fn known_vector_empty() {
        assert_eq!(fnv1a_64(b""), 0xcbf2_9ce4_8422_2325);
    }

    #[test]
    fn deterministic_and_input_sensitive() {
        assert_eq!(fnv1a_64(b"abc"), fnv1a_64(b"abc"));
        assert_ne!(fnv1a_64(b"abc"), fnv1a_64(b"abd"));
    }
}
