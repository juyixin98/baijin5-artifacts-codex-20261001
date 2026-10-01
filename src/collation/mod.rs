//! Collation rules and sort/equivalence keys.

mod key;
mod rules;

pub use rules::{Rule, V1, V2};

pub(crate) use key::{GroupKey, Keyed};

/// Stateless, shareable collator bound to exactly one [`Rule`] version.
///
/// A collator is the single place that turns an *original string* into an
/// *aggregation key*. Executors never normalize strings themselves.
#[derive(Clone, Copy, Debug)]
pub struct Collator {
    rule: Rule,
}

impl Collator {
    pub fn new(rule: Rule) -> Self {
        Collator { rule }
    }

    pub fn for_version(version: u16) -> Option<Self> {
        Rule::from_version(version).map(Collator::new)
    }

    pub fn rule(&self) -> Rule {
        self.rule
    }

    pub fn version(&self) -> u16 {
        self.rule.version
    }

    /// Compute the aggregation key for one raw value.
    pub(crate) fn key_of(&self, raw: &str) -> Keyed {
        self.rule.key_of(raw)
    }

    /// Stable textual rendering of the aggregation key (version + fragments),
    /// suitable for diagnostics and for asserting cross-version key
    /// distinctness. It is a *fingerprint of the key*, not the raw value.
    pub fn key_fingerprint(&self, raw: &str) -> String {
        let k = self.key_of(raw);
        if k.key.null {
            return format!("v{}:<null>", self.rule.version);
        }
        format!("v{}:{:?}", self.rule.version, k.key.frags)
    }
}
