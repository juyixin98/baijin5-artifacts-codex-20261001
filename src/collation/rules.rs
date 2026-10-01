//! Collation rule versions, the sort-key representation, and canonical byte encoding.

use crate::collation::fnv::fnv1a_64;
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::sync::OnceLock;

/// Identifies a rule set. Equality of keys REQUIRES the same version; mixing values
/// grouped under different versions is rejected (see `exec` validation).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum RuleVersion {
    /// 2026R1: NFC, case-insensitive, accent-insensitive, natural numeric ordering.
    #[serde(rename = "2026R1")]
    V2026R1,
    /// 2026R2: NFC, case-sensitive, accent-sensitive, natural numeric ordering.
    #[serde(rename = "2026R2")]
    V2026R2,
}

impl RuleVersion {
    pub fn as_str(self) -> &'static str {
        match self {
            RuleVersion::V2026R1 => "2026R1",
            RuleVersion::V2026R2 => "2026R2",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        rules()
            .iter()
            .find(|r| r.version.as_str() == s)
            .map(|r| r.version)
    }
}

impl std::fmt::Display for RuleVersion {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Unicode normalization applied before folding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Normalization {
    None,
    Nfc,
}

/// A complete, typed collation rule set.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CollationRule {
    pub version: RuleVersion,
    pub normalization: Normalization,
    pub case_insensitive: bool,
    pub accent_insensitive: bool,
    /// When true, ASCII digit runs compare numerically (`a2 < a10`).
    pub numeric_sequences: bool,
}

/// How an equivalence class keeps its representative ORIGINAL value. The choice of
/// representative never affects grouping/hashing — only the value surfaced to callers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum RepresentativePolicy {
    /// Keep the first original value encountered in input order.
    #[default]
    FirstWins,
    /// Keep the last original value encountered.
    LastWins,
    /// Keep the shortest original value (ties broken by first occurrence).
    ShortestWins,
}

/// One element of a [`SortKey`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum KeyElement {
    /// Folded non-digit text chunk.
    Text(String),
    /// Canonical decimal form of a digit run (leading zeros removed).
    Number(String),
}

/// The typed sort/equality key. Equality and total order both come from this struct,
/// guaranteeing consistent equality and ordering semantics.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SortKey {
    pub version: RuleVersion,
    pub elements: Vec<KeyElement>,
}

// Note: `PartialEq`/`Eq` are derived; `Ord` is implemented so numbers and text have a
// defined cross-type order (digit runs order before letters, mirroring ASCII).
impl Ord for KeyElement {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (KeyElement::Number(a), KeyElement::Number(b)) => {
                // Canonical forms have no leading zeros, so length then lex order works.
                a.len().cmp(&b.len()).then_with(|| a.cmp(b))
            }
            (KeyElement::Text(a), KeyElement::Text(b)) => a.cmp(b),
            // Numeric before text at the same position ("a1..." < "aa...").
            (KeyElement::Number(_), KeyElement::Text(_)) => Ordering::Less,
            (KeyElement::Text(_), KeyElement::Number(_)) => Ordering::Greater,
        }
    }
}
impl PartialOrd for KeyElement {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Static registry of supported rule versions.
pub static RULE_REGISTRY: OnceLock<Vec<CollationRule>> = OnceLock::new();

/// Initialize and return the rule registry.
pub fn rules() -> &'static [CollationRule] {
    RULE_REGISTRY.get_or_init(|| {
        vec![
            CollationRule {
                version: RuleVersion::V2026R1,
                normalization: Normalization::Nfc,
                case_insensitive: true,
                accent_insensitive: true,
                numeric_sequences: true,
            },
            CollationRule {
                version: RuleVersion::V2026R2,
                normalization: Normalization::Nfc,
                case_insensitive: false,
                accent_insensitive: false,
                numeric_sequences: true,
            },
        ]
    })
}

/// Look up a rule by version.
pub fn rule_for(version: RuleVersion) -> Option<&'static CollationRule> {
    rules().iter().find(|r| r.version == version)
}

/// Prefix tags for the canonical byte encoding.
const TAG_NUMBER: u8 = 1;
const TAG_TEXT: u8 = 2;

/// Collision-resistant canonical encoding of a [`SortKey`].
///
/// Layout: `version_byte, (tag, varint_len, bytes)*`. Lengths make the concatenation
/// unambiguous (`ab|c` cannot masquerade as `a|bc`). This byte string is the single
/// source of truth for both the hash-agg bucket hash and any external deduplication;
/// equivalent values ALWAYS encode identically, so their hashes are compatible.
pub fn canonical_bytes(key: &SortKey) -> Vec<u8> {
    let mut out = Vec::with_capacity(16);
    out.push(version_byte(key.version));
    for el in &key.elements {
        let (tag, payload) = match el {
            KeyElement::Number(s) => (TAG_NUMBER, s.as_bytes()),
            KeyElement::Text(s) => (TAG_TEXT, s.as_bytes()),
        };
        out.push(tag);
        write_varint(&mut out, payload.len() as u64);
        out.extend_from_slice(payload);
    }
    out
}

/// Deterministic 64-bit hash of a sort key — the hash-agg bucket function.
pub fn key_hash(key: &SortKey) -> u64 {
    fnv1a_64(&canonical_bytes(key))
}

fn version_byte(v: RuleVersion) -> u8 {
    match v {
        RuleVersion::V2026R1 => 1,
        RuleVersion::V2026R2 => 2,
    }
}

fn write_varint(out: &mut Vec<u8>, mut n: u64) {
    loop {
        let mut byte = (n & 0x7f) as u8;
        n >>= 7;
        if n != 0 {
            byte |= 0x80;
        }
        out.push(byte);
        if n == 0 {
            break;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_encoding_is_length_delimited() {
        let k1 = SortKey {
            version: RuleVersion::V2026R1,
            elements: vec![KeyElement::Text("ab".into()), KeyElement::Text("c".into())],
        };
        let k2 = SortKey {
            version: RuleVersion::V2026R1,
            elements: vec![KeyElement::Text("a".into()), KeyElement::Text("bc".into())],
        };
        assert_ne!(canonical_bytes(&k1), canonical_bytes(&k2));
    }

    #[test]
    fn version_byte_participates_in_encoding() {
        let elements = vec![KeyElement::Text("cafe".into())];
        let r1 = SortKey {
            version: RuleVersion::V2026R1,
            elements: elements.clone(),
        };
        let r2 = SortKey {
            version: RuleVersion::V2026R2,
            elements,
        };
        assert_ne!(canonical_bytes(&r1), canonical_bytes(&r2));
        assert_ne!(key_hash(&r1), key_hash(&r2));
    }
}
