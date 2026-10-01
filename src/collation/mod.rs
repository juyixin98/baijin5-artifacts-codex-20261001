//! Custom-collation core: rule versions, sort keys, equivalence, canonical hashing.
//!
//! Contract highlights:
//! * equality and ordering come from the SAME [`SortKey`] (consistent equality/order);
//! * a sort-equivalence class keeps a *representative original value* (see
//!   [`RepresentativePolicy`]), the aggregation key is never confused with the identity;
//! * the [`canonical_bytes`] encoding feeds a deterministic FNV-1a hash so hash grouping
//!   produces compatible hashes for equivalent values;
//! * the rule version is part of the key, so different rule versions can never mix.

mod fnv;
mod rules;

pub use fnv::fnv1a_64;
pub use rules::{
    canonical_bytes, key_hash, rule_for, rules, CollationRule, KeyElement, Normalization,
    RepresentativePolicy, RuleVersion, SortKey, RULE_REGISTRY,
};

use std::cmp::Ordering;

/// Build the typed sort key for `value` under `rule`.
///
/// The key is a sequence of [`KeyElement`]s. ASCII digit runs become numeric elements
/// (natural / "number aware" ordering: `a2 < a10`); everything else is case/accent
/// folded text according to the rule version.
pub fn sort_key(rule: &CollationRule, value: &str) -> SortKey {
    let normalized = normalize(rule, value);
    let mut elements: Vec<KeyElement> = Vec::new();
    let mut text = String::new();
    let mut digits = String::new();

    for ch in normalized.chars() {
        if rule.numeric_sequences && ch.is_ascii_digit() {
            if !text.is_empty() {
                elements.push(KeyElement::Text(std::mem::take(&mut text)));
            }
            digits.push(ch);
        } else {
            if !digits.is_empty() {
                elements.push(KeyElement::Number(canonical_digits(&digits)));
                digits.clear();
            }
            text.push(ch);
        }
    }
    if !digits.is_empty() {
        elements.push(KeyElement::Number(canonical_digits(&digits)));
    } else if !text.is_empty() {
        elements.push(KeyElement::Text(text));
    }

    SortKey {
        version: rule.version,
        elements,
    }
}

/// Normalize + fold a value into the string scanned by [`sort_key`].
///
/// Scope (deliberate, documented in README): Unicode NFC, Rust `to_lowercase` case
/// folding, and stripping of the Combining Diacritical Marks block U+0300..U+036F.
/// Only ASCII digit runs are treated as numeric elements.
pub fn normalize(rule: &CollationRule, value: &str) -> String {
    use unicode_normalization::UnicodeNormalization;

    // 1. Unicode normalization.
    let nfc: String = match rule.normalization {
        Normalization::Nfc => value.nfc().collect(),
        Normalization::None => value.to_owned(),
    };

    // 2. Accent treatment: NFD decomposition, drop combining marks, recompose.
    let folded = if rule.accent_insensitive {
        let decomposed: String = nfc.nfd().collect();
        let stripped: String = decomposed
            .chars()
            .filter(|c| !(0x0300..=0x036F).contains(&(*c as u32)))
            .collect();
        stripped.nfc().collect()
    } else {
        nfc
    };

    // 3. Case.
    if rule.case_insensitive {
        folded.to_lowercase()
    } else {
        folded
    }
}

/// Leading-zero-insensitive canonical digit string ("007" -> "7", "" -> "0").
fn canonical_digits(raw: &str) -> String {
    let trimmed = raw.trim_start_matches('0');
    if trimmed.is_empty() {
        "0".to_owned()
    } else {
        trimmed.to_owned()
    }
}

impl SortKey {
    /// Collation equality: same rule version AND same key elements.
    pub fn equivalent(&self, other: &SortKey) -> bool {
        self.version == other.version && self.elements == other.elements
    }

    /// Total ordering consistent with [`SortKey::equivalent`].
    pub fn compare(&self, other: &SortKey) -> Ordering {
        match self.version.cmp(&other.version) {
            Ordering::Equal => self.elements.cmp(&other.elements),
            ne => ne,
        }
    }
}

impl Ord for SortKey {
    fn cmp(&self, other: &Self) -> Ordering {
        self.compare(other)
    }
}
impl PartialOrd for SortKey {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
