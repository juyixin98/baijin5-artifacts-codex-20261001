//! Versioned binary wire format for IBLT tables (see `docs/protocol.md`).
//!
//! Layout, all integers little-endian:
//!
//! ```text
//! offset  size  field
//! 0       4     magic "IBLT"
//! 4       2     version (= 1)
//! 6       2     flags (must be 0)
//! 8       4     k
//! 12      4     n_cells
//! 16      8     seed
//! 24      8     reserved (must be 0)
//! 32      ...   n_cells cells, each 24 bytes: count i64, key_sum u64, hash_sum u64
//! ```
//!
//! Parsing is strict: any framing violation is an `InvalidInput` error,
//! and a declared cell count above the configured limit is
//! `ResourceExhausted`. The parser never trusts the declared length
//! against the actual buffer length.

use crate::error::IbltError;
use crate::iblt::{Cell, Iblt, Params};
use crate::limits::Limits;

pub const MAGIC: [u8; 4] = *b"IBLT";
pub const VERSION: u16 = 1;
pub const HEADER_LEN: usize = 32;
pub const CELL_LEN: usize = 24;

/// Serialize a table to its canonical binary form. Deterministic: the
/// same table state always produces the same bytes.
pub fn serialize(table: &Iblt) -> Vec<u8> {
    let p = table.params();
    let mut out = Vec::with_capacity(HEADER_LEN + CELL_LEN * p.cells as usize);
    out.extend_from_slice(&MAGIC);
    out.extend_from_slice(&VERSION.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes()); // flags
    out.extend_from_slice(&p.k.to_le_bytes());
    out.extend_from_slice(&p.cells.to_le_bytes());
    out.extend_from_slice(&p.seed.to_le_bytes());
    out.extend_from_slice(&0u64.to_le_bytes()); // reserved
    for c in table.cells() {
        out.extend_from_slice(&c.count.to_le_bytes());
        out.extend_from_slice(&c.key_sum.to_le_bytes());
        out.extend_from_slice(&c.hash_sum.to_le_bytes());
    }
    out
}

/// Parse a table from bytes, enforcing `limits`.
pub fn parse(bytes: &[u8], limits: &Limits) -> Result<Iblt, IbltError> {
    if bytes.len() < HEADER_LEN {
        return Err(IbltError::InvalidInput(format!(
            "truncated header: need {HEADER_LEN} bytes, got {}",
            bytes.len()
        )));
    }
    if bytes[0..4] != MAGIC {
        return Err(IbltError::InvalidInput("bad magic: not an IBLT table".into()));
    }
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    if version != VERSION {
        return Err(IbltError::InvalidInput(format!(
            "unsupported format version {version} (this build understands {VERSION})"
        )));
    }
    let flags = u16::from_le_bytes([bytes[6], bytes[7]]);
    if flags != 0 {
        return Err(IbltError::InvalidInput(format!(
            "unsupported flags {flags:#06x}"
        )));
    }
    let k = u32::from_le_bytes(bytes[8..12].try_into().unwrap());
    let n_cells = u32::from_le_bytes(bytes[12..16].try_into().unwrap());
    let seed = u64::from_le_bytes(bytes[16..24].try_into().unwrap());
    let reserved = u64::from_le_bytes(bytes[24..32].try_into().unwrap());
    if reserved != 0 {
        return Err(IbltError::InvalidInput("reserved header field must be 0".into()));
    }

    limits.check_cell_count(n_cells as usize)?;

    let expected = HEADER_LEN + CELL_LEN * n_cells as usize;
    if bytes.len() != expected {
        return Err(IbltError::InvalidInput(format!(
            "length mismatch: header declares {n_cells} cells ({expected} bytes total) \
             but buffer has {} bytes",
            bytes.len()
        )));
    }

    let params = Params { cells: n_cells, k, seed };
    let mut cells = Vec::with_capacity(n_cells as usize);
    for i in 0..n_cells as usize {
        let at = HEADER_LEN + i * CELL_LEN;
        let chunk = &bytes[at..at + CELL_LEN];
        cells.push(Cell {
            count: i64::from_le_bytes(chunk[0..8].try_into().unwrap()),
            key_sum: u64::from_le_bytes(chunk[8..16].try_into().unwrap()),
            hash_sum: u64::from_le_bytes(chunk[16..24].try_into().unwrap()),
        });
    }
    Iblt::from_cells(params, cells)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hash::{DEFAULT_K, DEFAULT_SEED};

    fn sample_table() -> Iblt {
        let mut t = Iblt::new(Params { cells: 16, k: DEFAULT_K, seed: DEFAULT_SEED }).unwrap();
        for key in [10, 20, 30, 40] {
            t.insert(key);
        }
        t
    }

    #[test]
    fn roundtrip_preserves_cells() {
        let t = sample_table();
        let bytes = serialize(&t);
        let back = parse(&bytes, &Limits::default()).unwrap();
        assert_eq!(t.params(), back.params());
        assert_eq!(t.cells(), back.cells());
    }

    #[test]
    fn serialization_is_deterministic() {
        assert_eq!(serialize(&sample_table()), serialize(&sample_table()));
    }

    #[test]
    fn rejects_bad_magic() {
        let mut bytes = serialize(&sample_table());
        bytes[0] = b'X';
        let err = parse(&bytes, &Limits::default()).unwrap_err();
        assert_eq!(err.category(), crate::error::ErrorCategory::InvalidInput);
    }

    #[test]
    fn rejects_truncation_and_extension() {
        let bytes = serialize(&sample_table());
        assert!(parse(&bytes[..bytes.len() - 1], &Limits::default()).is_err());
        let mut longer = bytes.clone();
        longer.push(0);
        assert!(parse(&longer, &Limits::default()).is_err());
    }

    #[test]
    fn rejects_cell_count_above_limit() {
        let bytes = serialize(&sample_table());
        let limits = Limits { max_cells: 4, ..Limits::default() };
        let err = parse(&bytes, &limits).unwrap_err();
        assert_eq!(err.category(), crate::error::ErrorCategory::ResourceExhausted);
    }
}
