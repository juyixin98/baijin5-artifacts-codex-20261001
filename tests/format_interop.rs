//! Interop tests against the committed binary fixtures.
//!
//! The reference answers (`fixtures/expected_only_*.txt`) are produced by
//! `scripts/gen_expected.sh` with sort/comm — not by the Rust kernel —
//! so these tests check the kernel against an outside computation, and
//! pin the binary format byte-for-byte.

use std::fs;
use std::path::PathBuf;

use iblt_service::error::ErrorCategory;
use iblt_service::format;
use iblt_service::iblt::{Iblt, Params};
use iblt_service::limits::Limits;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures").join(name)
}

fn read_keys(name: &str) -> Vec<u64> {
    fs::read_to_string(fixture(name))
        .unwrap_or_else(|e| panic!("read fixture {name}: {e}"))
        .split_whitespace()
        .map(|s| s.parse::<u64>().expect("fixture keys are u64"))
        .collect()
}

fn fixture_params() -> Params {
    let text = fs::read_to_string(fixture("params.json")).expect("read params.json");
    let v: serde_json::Value = serde_json::from_str(&text).expect("parse params.json");
    Params {
        cells: v["cells"].as_u64().unwrap() as u32,
        k: v["k"].as_u64().unwrap() as u32,
        seed: v["seed"].as_u64().unwrap(),
    }
}

#[test]
fn fixture_tables_decode_to_externally_computed_diff() {
    let limits = Limits::default();
    let a_bytes = fs::read(fixture("table_a_v1.iblt")).expect("read table_a fixture");
    let b_bytes = fs::read(fixture("table_b_v1.iblt")).expect("read table_b fixture");
    let a = format::parse(&a_bytes, &limits).expect("parse table_a");
    let b = format::parse(&b_bytes, &limits).expect("parse table_b");

    let report = a.subtract(&b).unwrap().decode().expect("fixture diff decodes");

    assert_eq!(report.only_a, read_keys("expected_only_a.txt"));
    assert_eq!(report.only_b, read_keys("expected_only_b.txt"));
}

#[test]
fn encoding_is_byte_deterministic_against_committed_fixture() {
    // Re-encoding the fixture keys with the fixture params must reproduce
    // the committed bytes exactly: this pins the binary format and the
    // hash functions against silent drift.
    let params = fixture_params();
    for side in ["a", "b"] {
        let mut table = Iblt::new(params).unwrap();
        for key in read_keys(&format!("keys_{side}.txt")) {
            table.insert(key);
        }
        let expected = fs::read(fixture(&format!("table_{side}_v1.iblt"))).unwrap();
        assert_eq!(
            format::serialize(&table),
            expected,
            "re-encoded table_{side} differs from committed fixture"
        );
    }
}

#[test]
fn fixture_header_fields_match_params_json() {
    let bytes = fs::read(fixture("table_a_v1.iblt")).expect("read fixture");
    assert_eq!(&bytes[0..4], b"IBLT");
    assert_eq!(u16::from_le_bytes([bytes[4], bytes[5]]), 1);
    let p = fixture_params();
    let parsed = format::parse(&bytes, &Limits::default()).unwrap();
    assert_eq!(parsed.params(), p);
}

#[test]
fn parser_rejects_framing_violations_by_category() {
    let bytes = fs::read(fixture("table_a_v1.iblt")).expect("read fixture");
    let limits = Limits::default();

    // bad magic
    let mut bad = bytes.clone();
    bad[1] = b'X';
    assert_eq!(
        format::parse(&bad, &limits).unwrap_err().category(),
        ErrorCategory::InvalidInput
    );
    // unsupported version
    let mut bad = bytes.clone();
    bad[4] = 99;
    assert_eq!(
        format::parse(&bad, &limits).unwrap_err().category(),
        ErrorCategory::InvalidInput
    );
    // truncated body
    assert_eq!(
        format::parse(&bytes[..bytes.len() - 8], &limits)
            .unwrap_err()
            .category(),
        ErrorCategory::InvalidInput
    );
    // declared cells above limit -> resource exhaustion, not input error
    let tight = Limits { max_cells: 4, ..Limits::default() };
    assert_eq!(
        format::parse(&bytes, &tight).unwrap_err().category(),
        ErrorCategory::ResourceExhausted
    );
}
