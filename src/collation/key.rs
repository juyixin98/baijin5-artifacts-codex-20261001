//! Sort-key / equivalence-class definition.
//!
//! A [`GroupKey`] is the *aggregation key*: it is deliberately distinct from
//! the original string value (the identity carried by each input row). Two raw
//! strings with equal `GroupKey`s fall in the same sort-equivalence class and
//! hash to the same bucket; the raw representative value is retained
//! separately by the executors.

use std::hash::{Hash, Hasher};

/// One token of a collation key.
///
/// Digit runs become [`Frag::Num`] so that natural numeric ordering applies
/// (`file2 < file10`); everything else is a normalized UTF-8 text fragment.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Frag {
    /// `Num` is declared first so that at a text/number boundary ordering stays
    /// total; in practice boundaries are aligned (`file2` vs `file10`).
    Num(u64),
    Text(Vec<u8>),
}

// Manual Hash so it stays consistent with the derived `Eq`.
impl Hash for Frag {
    fn hash<H: Hasher>(&self, state: &mut H) {
        match self {
            Frag::Num(n) => {
                0u8.hash(state);
                n.hash(state);
            }
            Frag::Text(b) => {
                1u8.hash(state);
                b.hash(state);
            }
        }
    }
}

/// Aggregation key for one value under one specific rule version.
///
/// * `rule` is part of the key on purpose: keys produced by different rule
///   versions are never equal, so they can never land in one group even if a
///   caller mistakenly mixes them.
/// * `null` keys form their own class (documented NULL policy, see README).
/// * Equality/order/hash consider only `(rule, null, frags)`. The numeric
///   overflow flag is *not* part of key identity; it is reported alongside the
///   key by the tokenizer so the validator can return "undetermined".
#[derive(Clone, Debug)]
pub(crate) struct GroupKey {
    pub rule: u16,
    pub null: bool,
    pub frags: Vec<Frag>,
}

impl GroupKey {
    pub fn null_key(rule: u16) -> Self {
        GroupKey {
            rule,
            null: true,
            frags: Vec::new(),
        }
    }

    pub fn new(rule: u16, frags: Vec<Frag>) -> Self {
        GroupKey {
            rule,
            null: false,
            frags,
        }
    }
}

impl PartialEq for GroupKey {
    fn eq(&self, other: &Self) -> bool {
        self.rule == other.rule && self.null == other.null && self.frags == other.frags
    }
}

impl Eq for GroupKey {}

impl Hash for GroupKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.rule.hash(state);
        self.null.hash(state);
        self.frags.hash(state);
    }
}

impl Ord for GroupKey {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        use std::cmp::Ordering;
        match self.rule.cmp(&other.rule) {
            Ordering::Equal => {}
            o => return o,
        }
        // NULL sorts after every real value.
        match (self.null, other.null) {
            (true, true) => Ordering::Equal,
            (true, false) => Ordering::Greater,
            (false, true) => Ordering::Less,
            (false, false) => self.frags.cmp(&other.frags),
        }
    }
}

impl PartialOrd for GroupKey {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

/// Result of tokenizing one raw value.
pub(crate) struct Keyed {
    pub key: GroupKey,
    /// True when a digit run did not fit in `u64` and saturated. Such a key is
    /// still computed (deterministic), but the contract verdict is
    /// "undetermined" because distinct values may have collapsed.
    pub overflow: bool,
}

/// Tokenize a normalized lowercased string into collation fragments.
///
/// ASCII digit runs fold to `u64` (natural-number semantics, leading zeros
/// ignored); remaining characters are concatenated into text fragments.
pub(crate) fn tokenize(rule: u16, normalized: &str) -> Keyed {
    let mut frags: Vec<Frag> = Vec::new();
    let mut overflow = false;
    let mut text = String::new();
    let mut num: u64 = 0;
    let mut in_num = false;

    let flush_text = |t: &mut String, frags: &mut Vec<Frag>| {
        if !t.is_empty() {
            frags.push(Frag::Text(std::mem::take(t).into_bytes()));
        }
    };

    for c in normalized.chars() {
        if c.is_ascii_digit() {
            if !in_num {
                flush_text(&mut text, &mut frags);
                in_num = true;
                num = 0;
            }
            let digit = u64::from(c as u8 - b'0');
            match num.checked_mul(10).and_then(|v| v.checked_add(digit)) {
                Some(v) => num = v,
                None => {
                    num = u64::MAX;
                    overflow = true;
                }
            }
        } else {
            if in_num {
                frags.push(Frag::Num(num));
                in_num = false;
            }
            text.push(c);
        }
    }
    if in_num {
        frags.push(Frag::Num(num));
    } else {
        flush_text(&mut text, &mut frags);
    }

    Keyed {
        key: GroupKey::new(rule, frags),
        overflow,
    }
}
