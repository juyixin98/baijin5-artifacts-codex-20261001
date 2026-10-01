//! Shared test support: synthetic fixtures and an INDEPENDENT oracle.
//!
//! Nothing in this module calls the crate's collation/operator code. Expected
//! answers are either literal values or produced by a second, separately
//! written implementation, so a self-consistent bug in the core cannot make
//! the tests pass by itself.

#![allow(dead_code)]

use std::collections::BTreeMap;

/// A synthetic input row as `(record_id, Option<value>)`.
pub type FixRow = (&'static str, Option<&'static str>);

/// Accent/case/composed-vs-decomposed fixture.
pub fn accent_rows() -> Vec<FixRow> {
    vec![
        ("r1", Some("Café")),         // composed é
        ("r2", Some("cafe\u{0301}")), // decomposed e + combining acute
        ("r3", Some("CAFE")),
        ("r4", Some("naïve")),
        ("r5", Some("NAIVE")),
        ("r6", Some("NAI\u{0308}VE")), // decomposed diaeresis
        ("r7", None),                  // NULL identity
    ]
}

/// Independently hand-authored expectation for [`accent_rows`] under v1.
/// Two real classes (cafe, naive) plus the NULL class = 3 groups.
pub const ACCENT_EXPECTED_GROUPS: usize = 3;
pub const ACCENT_EXPECTED_DISTINCT: &[&str] = &["Café", "naïve", ""];

/// Numeric natural-order fixture.
pub fn numeric_rows() -> Vec<FixRow> {
    vec![
        ("n1", Some("file2")),
        ("n2", Some("file10")),
        ("n3", Some("file2")),
        ("n4", Some("item1")),
        ("n5", Some("item01")), // leading zero -> same number 1
        ("n6", Some("item10")),
        ("n7", Some("a1b2")),
        ("n8", Some("a01b02")), // same numeric tokens
    ]
}

/// Independently authored: a1b2, file2, file10, item1, item10 = 5 classes,
/// in natural key order (text "a" < "file" < "item"; numbers natural).
pub const NUMERIC_EXPECTED_GROUPS: usize = 5;
pub const NUMERIC_EXPECTED_DISTINCT: &[&str] = &["a1b2", "file2", "file10", "item1", "item10"];

/// Mixed-script normalization stress (compatibility forms).
pub fn normalization_rows() -> Vec<FixRow> {
    vec![
        ("z1", Some("ﬀoo")), // ligature ﬀ -> ff under NFKD
        ("z2", Some("ffoo")),
        ("z3", Some("FFOO")),
        ("z4", Some("café")),
    ]
}
pub const NORMALIZATION_EXPECTED_GROUPS: usize = 2; // ffoo, cafe

// ---------------------------------------------------------------------------
// Independent oracle for v1 (separate implementation, not the crate's).
// ---------------------------------------------------------------------------

#[derive(Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum Tok {
    T(String),
    N(u128), // wider than the core's u64 on purpose: oracle detects saturation
}

/// Independent accent folding: explicit table for the marks the fixtures use,
/// avoiding the NFKD code path the implementation relies on.
fn strip_accent(c: char) -> char {
    match c {
        'é' | 'É' | 'è' | 'ê' => 'e',
        'ï' | 'Ï' | 'î' => 'i',
        'ä' | 'à' | 'â' | 'á' => 'a',
        'ö' | 'ô' | 'ó' => 'o',
        'ü' | 'û' | 'ú' => 'u',
        'ç' | 'Ç' => 'c',
        'ﬀ' => '\u{FFFD}', // handled specially by the ligature case below
        other => other,
    }
}

/// Independently normalize one value for the v1-equivalent semantics.
pub fn oracle_norm(input: &str) -> String {
    let mut out = String::new();
    // Compatibility ligatures expanded explicitly.
    let expanded = input.replace('ﬀ', "ff");
    for c in expanded.chars() {
        // Skip combining diacritical marks (decomposed inputs).
        if ('\u{0300}'..='\u{036F}').contains(&c) {
            continue;
        }
        let folded = strip_accent(c);
        for lc in folded.to_lowercase() {
            out.push(lc);
        }
    }
    out
}

fn oracle_tokens(input: &str) -> Vec<Tok> {
    let norm = oracle_norm(input);
    let mut toks = Vec::new();
    let mut text = String::new();
    let mut num: u128 = 0;
    let mut in_num = false;
    for c in norm.chars() {
        if c.is_ascii_digit() {
            if !in_num {
                if !text.is_empty() {
                    toks.push(Tok::T(std::mem::take(&mut text)));
                }
                in_num = true;
                num = 0;
            }
            num = num * 10 + u128::from(c as u8 - b'0');
        } else {
            if in_num {
                toks.push(Tok::N(num));
                in_num = false;
            }
            text.push(c);
        }
    }
    if in_num {
        toks.push(Tok::N(num));
    } else if !text.is_empty() {
        toks.push(Tok::T(text));
    }
    toks
}

/// Independently compute equivalence classes; returns representatives (first
/// raw value in input order) keyed by the oracle token vector, plus per-class
/// sizes. NULL maps to its own class.
pub fn oracle_group_v1<A, B>(rows: &[(A, Option<B>)]) -> BTreeMap<Vec<Tok>, (String, usize, bool)>
where
    A: AsRef<str>,
    B: AsRef<str>,
{
    let mut map: BTreeMap<Vec<Tok>, (String, usize, bool)> = BTreeMap::new();
    for (_id, v) in rows {
        let toks = match v {
            None => vec![Tok::T("__null__".into())],
            Some(s) => oracle_tokens(s.as_ref()),
        };
        let entry = map.entry(toks).or_insert_with(|| {
            (
                v.as_ref()
                    .map(|s| s.as_ref().to_string())
                    .unwrap_or_default(),
                0,
                v.is_none(),
            )
        });
        entry.1 += 1;
    }
    map
}

/// Oracle class count for v1.
pub fn oracle_group_count_v1<A, B>(rows: &[(A, Option<B>)]) -> usize
where
    A: AsRef<str>,
    B: AsRef<str>,
{
    oracle_group_v1(rows).len()
}

/// Deterministic pseudo-random strings for cross-property checks (seeded, no
/// external data).
pub fn synth_strings(seed: u64, n: usize) -> Vec<String> {
    let mut x = seed;
    let mut next = || {
        // xorshift64
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        x
    };
    let alphabet = ['a', 'A', 'e', 'é', 'E', '2', '1', '0', 'ï', ' ', 'z'];
    (0..n)
        .map(|_| {
            let len = (next() % 6) as usize + 1;
            (0..len)
                .map(|_| alphabet[(next() as usize) % alphabet.len()])
                .collect()
        })
        .collect()
}
