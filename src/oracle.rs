//! Independent reference oracle for partition truth.
//!
//! IMPORTANT: this oracle deliberately does NOT call the code under test. It does not
//! use [`crate::collation::sort_key`], [`crate::collation::key_hash`], or the
//! [`crate::exec`] operators. Instead it independently:
//!   1. builds equivalence signatures with its own folding implementation
//!      (std `char` based accent stripping + a separate lowercase step),
//!   2. partitions with an O(n^2) union-by-pair algorithm instead of sort/hash,
//!   3. derives expected group count and membership from those signatures.
//!
//! Its numeric handling is intentionally simpler and matches the documented scope
//! (ASCII digit runs compared by u128 value; runs longer than 38 digits fall back to
//! lex comparison of the trimmed digit string, which agrees with the core for the
//! fixture ranges used).

use crate::collation::{CollationRule, Normalization, RuleVersion};

/// Independent signature element (mirrors the CONTRACT, not the core's code).
#[derive(Debug, PartialEq, Eq, Hash, Clone)]
pub enum OracleElem {
    T(String),
    /// Numeric run as the pair (digit count, trimmed decimal string) so "02" == "2".
    N(usize, String),
}

/// Independent fold: signature plus the version byte.
pub fn signature(version: RuleVersion, value: &str) -> Vec<OracleElem> {
    use unicode_normalization::UnicodeNormalization;
    let rule = match version {
        RuleVersion::V2026R1 => CollationRule {
            version,
            normalization: Normalization::Nfc,
            case_insensitive: true,
            accent_insensitive: true,
            numeric_sequences: true,
        },
        RuleVersion::V2026R2 => CollationRule {
            version,
            normalization: Normalization::Nfc,
            case_insensitive: false,
            accent_insensitive: false,
            numeric_sequences: true,
        },
    };

    // Independent normalization pipeline (re-derived, not reusing core::normalize).
    let s: String = value.nfc().collect();
    let s: String = if rule.accent_insensitive {
        s.nfd()
            .filter(|c| !(0x0300..=0x036F).contains(&(*c as u32)))
            .collect::<String>()
            .nfc()
            .collect()
    } else {
        s
    };
    let s: String = if rule.case_insensitive {
        s.to_lowercase()
    } else {
        s
    };

    let mut out: Vec<OracleElem> = Vec::new();
    let mut text = String::new();
    let mut digits = String::new();
    for c in s.chars() {
        if rule.numeric_sequences && c.is_ascii_digit() {
            if !text.is_empty() {
                out.push(OracleElem::T(std::mem::take(&mut text)));
            }
            digits.push(c);
        } else {
            if !digits.is_empty() {
                let trimmed = digits.trim_start_matches('0');
                let trimmed = if trimmed.is_empty() { "0" } else { trimmed };
                out.push(OracleElem::N(trimmed.len(), trimmed.to_string()));
                digits.clear();
            }
            text.push(c);
        }
    }
    if !digits.is_empty() {
        let trimmed = digits.trim_start_matches('0');
        let trimmed = if trimmed.is_empty() { "0" } else { trimmed };
        out.push(OracleElem::N(trimmed.len(), trimmed.to_string()));
    } else if !text.is_empty() {
        out.push(OracleElem::T(text));
    }
    out
}

/// Returns true iff two values are equivalent under the version, per the oracle.
pub fn oracle_equivalent(version: RuleVersion, a: &str, b: &str) -> bool {
    signature(version, a) == signature(version, b)
}

/// Expected partition as groups of input indices (canonical: sorted by first member).
/// Built by union-find over ALL pairs — no sorting, no hashing.
pub fn oracle_partition(version: RuleVersion, values: &[String]) -> Vec<Vec<usize>> {
    let n = values.len();
    let mut parent: Vec<usize> = (0..n).collect();

    fn find(parent: &mut [usize], x: usize) -> usize {
        if parent[x] != x {
            parent[x] = find(parent, parent[x]);
        }
        parent[x]
    }
    fn union(parent: &mut [usize], a: usize, b: usize) {
        let ra = find(parent, a);
        let rb = find(parent, b);
        if ra != rb {
            // Always keep the smaller index as root for determinism.
            let (lo, hi) = (ra.min(rb), ra.max(rb));
            parent[hi] = lo;
        }
    }

    for i in 0..n {
        for j in (i + 1)..n {
            if oracle_equivalent(version, &values[i], &values[j]) {
                union(&mut parent, i, j);
            }
        }
    }

    let mut groups: std::collections::BTreeMap<usize, Vec<usize>> =
        std::collections::BTreeMap::new();
    for i in 0..n {
        let root = find(&mut parent, i);
        groups.entry(root).or_default().push(i);
    }
    groups.into_values().collect()
}

/// Expected number of distinct equivalence classes.
pub fn oracle_group_count(version: RuleVersion, values: &[String]) -> usize {
    oracle_partition(version, values).len()
}
