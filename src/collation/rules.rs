//! Named collation rule versions.
//!
//! Two deliberately different rules ship in the crate. They are *local,
//! synthetic* definitions — no locale database or external service:
//!
//! * [`V1`] (`accent_case_num@1`): Unicode NFKD, strip combining marks,
//!   lowercase, natural-number digit folding. This is the rule the acceptance
//!   fixtures exercise (accent/case equivalence, numeric sequences, composed
//!   vs decomposed normalization).
//! * [`V2`] (`binary@2`): raw Unicode scalar ordering, no case/accent folding,
//!   no numeric folding. Used to prove that switching rule versions changes
//!   the partition and that mixed-version input is rejected.

use super::key::{tokenize, Keyed};
use unicode_normalization::UnicodeNormalization;

/// Numeric id of the accent/case-insensitive numeric rule.
pub const V1: u16 = 1;
/// Numeric id of the binary rule.
pub const V2: u16 = 2;

/// An immutable, versioned collation rule.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rule {
    pub version: u16,
    pub name: &'static str,
    pub accent_insensitive: bool,
    pub case_insensitive: bool,
    pub numeric: bool,
}

impl Rule {
    pub fn v1() -> Self {
        Rule {
            version: V1,
            name: "accent_case_num@1",
            accent_insensitive: true,
            case_insensitive: true,
            numeric: true,
        }
    }

    pub fn v2() -> Self {
        Rule {
            version: V2,
            name: "binary@2",
            accent_insensitive: false,
            case_insensitive: false,
            numeric: false,
        }
    }

    /// Look a known rule up by its numeric version. Unknown versions are an
    /// explicit error rather than a silent fallback.
    pub fn from_version(version: u16) -> Option<Self> {
        match version {
            V1 => Some(Rule::v1()),
            V2 => Some(Rule::v2()),
            _ => None,
        }
    }

    /// Canonical human-readable descriptor, used in diagnostics.
    pub fn describe(self) -> String {
        format!("{} (v{})", self.name, self.version)
    }

    /// Normalize a raw value per this rule, then tokenize it into a
    /// [`super::key::GroupKey`].
    pub(crate) fn key_of(self, raw: &str) -> Keyed {
        let normalized = self.normalize(raw);
        if self.numeric {
            tokenize(self.version, &normalized)
        } else {
            // Non-numeric rule: the whole normalized string is one text
            // fragment, so digits order as ordinary characters.
            Keyed {
                key: super::key::GroupKey::new(
                    self.version,
                    vec![super::key::Frag::Text(normalized.into_bytes())],
                ),
                overflow: false,
            }
        }
    }

    fn normalize(self, raw: &str) -> String {
        if !self.accent_insensitive && !self.case_insensitive {
            // Binary rule: compare the raw scalar sequence verbatim.
            return raw.to_string();
        }
        let mut out = String::with_capacity(raw.len());
        // NFKD decomposes e.g. é into e + combining acute, and maps
        // compatibility forms; then combining marks are dropped.
        for c in raw.nfkd() {
            if self.accent_insensitive && is_combining_mark(c) {
                continue;
            }
            if self.case_insensitive {
                // Full Unicode case folding via to_lowercase (handles e.g.
                // 'İ', ligatures, Greek).
                for lc in c.to_lowercase() {
                    out.push(lc);
                }
            } else {
                out.push(c);
            }
        }
        out
    }
}

/// True for the Unicode Combining Diacritical Marks block (0x0300–0x036F).
/// NFKD puts the accents exercised by the fixtures in this block.
fn is_combining_mark(c: char) -> bool {
    ('\u{0300}'..='\u{036F}').contains(&c)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_version_is_none() {
        assert!(Rule::from_version(999).is_none());
        assert_eq!(Rule::from_version(V1).unwrap().version, V1);
    }

    #[test]
    fn v1_folds_accent_case_and_composition() {
        let r = Rule::v1();
        let a = r.key_of("Café").key;
        let b = r.key_of("cafe\u{0301}").key; // decomposed
        let c = r.key_of("CAFE").key;
        assert_eq!(a, b);
        assert_eq!(a, c);
    }

    #[test]
    fn v1_numeric_natural_order() {
        let r = Rule::v1();
        let k2 = r.key_of("file2").key;
        let k10 = r.key_of("file10").key;
        assert!(k2 < k10);
        assert_ne!(k2, k10);
    }

    #[test]
    fn v2_is_binary() {
        let r = Rule::v2();
        assert_ne!(r.key_of("Café").key, r.key_of("café").key);
        assert_ne!(r.key_of("file2").key, r.key_of("file10").key);
        // v1 and v2 keys are never equal even for identical raw input.
        assert_ne!(Rule::v1().key_of("abc").key, r.key_of("abc").key);
    }
}
