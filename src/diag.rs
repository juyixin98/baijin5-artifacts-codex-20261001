//! Decision diagnostics. Every request produces exactly one [`DiagRecord`]
//! explaining why it was accepted, rejected, or left undecidable, together
//! with the key adaptive state (p, list sizes, dirty count) before/after.
//!
//! Page ids are treated as potentially sensitive: by default records carry a
//! truncated SHA-256 fingerprint (`pg#<12 hex>`), never the raw id. Set
//! `log_raw_keys = true` in the config to disable redaction for debugging.

use std::collections::VecDeque;

use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::arc::{Outcome, PageId};
use crate::error::ErrorCategory;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Op {
    Read,
    Write,
    Resize,
    Snapshot,
    Restore,
}

/// The verdict attached to every request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum Decision {
    /// Request was served; `outcome` says from where (`None` for operations
    /// without a page outcome, e.g. resize).
    Accepted { outcome: Option<Outcome> },
    /// Request was definitively refused (not found, write-back stalled,
    /// malformed). Cache state was not mutated.
    Rejected {
        category: ErrorCategory,
        reason: String,
    },
    /// The engine could not determine the outcome (backing store I/O
    /// failure). Cache state was not mutated.
    Undecidable { reason: String },
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
pub struct ListSizes {
    pub t1: usize,
    pub t2: usize,
    pub b1: usize,
    pub b2: usize,
    pub dirty: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct DiagRecord {
    pub seq: u64,
    pub request_id: String,
    pub op: Op,
    /// Redacted key reference, or `null` for operations without a page key.
    pub key: Option<String>,
    pub decision: Decision,
    /// Human-readable justification (why accepted / rejected / undecidable).
    pub reason: String,
    pub p_before: usize,
    pub p_after: usize,
    pub capacity: usize,
    pub sizes_before: ListSizes,
    pub sizes_after: ListSizes,
}

/// Redact (or not) a page id for logging.
pub fn key_ref(page: PageId, raw: bool) -> String {
    if raw {
        format!("page:{page}")
    } else {
        let digest = Sha256::digest(page.to_be_bytes());
        let fp: String = digest[..6].iter().map(|b| format!("{b:02x}")).collect();
        format!("pg#{fp}")
    }
}

/// Bounded ring of recent decision records.
pub struct DiagLog {
    capacity: usize,
    records: VecDeque<DiagRecord>,
}

impl DiagLog {
    pub fn new(capacity: usize) -> Self {
        DiagLog {
            capacity: capacity.max(1),
            records: VecDeque::new(),
        }
    }

    pub fn push(&mut self, record: DiagRecord) {
        if self.records.len() == self.capacity {
            self.records.pop_front();
        }
        self.records.push_back(record);
    }

    /// Most recent `limit` records, optionally filtered by decision kind
    /// ("accepted" | "rejected" | "undecidable").
    pub fn query(&self, limit: usize, decision_kind: Option<&str>) -> Vec<&DiagRecord> {
        self.records
            .iter()
            .rev()
            .filter(|r| {
                matches!(
                    (decision_kind, &r.decision),
                    (None, _)
                        | (Some("accepted"), Decision::Accepted { .. })
                        | (Some("rejected"), Decision::Rejected { .. })
                        | (Some("undecidable"), Decision::Undecidable { .. })
                )
            })
            .take(limit)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect()
    }

    pub fn len(&self) -> usize {
        self.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redaction_hides_raw_page_id() {
        let redacted = key_ref(42, false);
        assert!(redacted.starts_with("pg#"));
        assert!(!redacted.contains("42"), "raw id must not appear: {redacted}");
        assert_eq!(key_ref(42, true), "page:42");
        // Stable across calls (same input -> same fingerprint).
        assert_eq!(redacted, key_ref(42, false));
        // Different ids -> different fingerprints.
        assert_ne!(key_ref(42, false), key_ref(43, false));
    }

    #[test]
    fn log_is_bounded() {
        let mut log = DiagLog::new(2);
        for seq in 0..5 {
            log.push(DiagRecord {
                seq,
                request_id: format!("r{seq}"),
                op: Op::Read,
                key: None,
                decision: Decision::Undecidable {
                    reason: "x".into(),
                },
                reason: "x".into(),
                p_before: 0,
                p_after: 0,
                capacity: 0,
                sizes_before: ListSizes::default(),
                sizes_after: ListSizes::default(),
            });
        }
        assert_eq!(log.len(), 2);
        let all = log.query(10, None);
        assert_eq!(all[0].seq, 3);
        assert_eq!(all[1].seq, 4);
    }
}
