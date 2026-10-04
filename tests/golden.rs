//! Golden interop tests: the reference answers come from an *independent*
//! Python implementation (tools/gen_fixtures.py), not from the Rust core.
//!
//! For every fixture case we assert both directions:
//!   decode(fixture_hex) == fixture values   (Rust decoder vs foreign bytes)
//!   encode(fixture values) == fixture_hex   (Rust encoder vs foreign bytes)

mod common;

use common::{input_fingerprint, load_manifest, TestLog};
use rbp_column::decode::{decode_column, DecodeBudget};
use rbp_column::encode::{encode_column, EncoderConfig};

#[test]
fn golden_decode_matches_reference_values() {
    let manifest = load_manifest();
    assert_eq!(manifest.format, "RBP1");
    assert_eq!(manifest.format_version, rbp_column::format::FORMAT_VERSION);

    for case in &manifest.cases {
        let mut log = TestLog::new(&format!("golden-decode:{}", case.name));
        let expected = case.expand_values();
        let bytes = hex::decode(&case.hex).expect("fixture hex must decode");
        log.step(&format!(
            "input_bytes={} expected_values={} fingerprint={} generator=\"{}\"",
            bytes.len(),
            expected.len(),
            input_fingerprint(&expected),
            manifest.generator
        ));

        let (values, stats) = decode_column(&bytes, &DecodeBudget::default())
            .unwrap_or_else(|e| panic!("case {}: decode failed: {e}", case.name));
        log.step(&format!(
            "decoded values={} blocks={} (rle={} bitpack={})",
            values.len(), stats.blocks, stats.rle_blocks, stats.bitpack_blocks
        ));

        // Per-value comparison against the reference column.
        assert_eq!(
            values.len(),
            expected.len(),
            "case {}: length mismatch",
            case.name
        );
        for (i, (got, want)) in values.iter().zip(expected.iter()).enumerate() {
            assert_eq!(got, want, "case {}: value {i} mismatch", case.name);
        }
        log.pass("per-value equality vs independent Python fixture");
    }
}

#[test]
fn golden_encode_matches_reference_bytes() {
    let manifest = load_manifest();
    let cfg = EncoderConfig {
        rle_min_run: manifest.rle_min_run,
    };

    for case in &manifest.cases {
        let mut log = TestLog::new(&format!("golden-encode:{}", case.name));
        let values = case.expand_values();
        let reference = hex::decode(&case.hex).expect("fixture hex must decode");
        log.step(&format!(
            "input_values={} fingerprint={}",
            values.len(),
            input_fingerprint(&values)
        ));

        let encoded = encode_column(&values, &cfg)
            .unwrap_or_else(|e| panic!("case {}: encode failed: {e}", case.name));
        log.step(&format!(
            "encoded {} bytes, reference {} bytes",
            encoded.len(),
            reference.len()
        ));

        assert_eq!(
            encoded, reference,
            "case {}: encoded bytes differ from independent reference",
            case.name
        );
        log.pass("byte-exact equality vs independent Python fixture");
    }
}
