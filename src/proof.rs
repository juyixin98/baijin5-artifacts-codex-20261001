//! Proof records: the wire format and the clause-node store.
//!
//! A proof is a JSON Lines stream:
//!
//! 1. Exactly one header, first: `{"type":"header","format_version":"1.0"}`.
//! 2. Zero or more input clauses (the CNF premises), all before derivations:
//!    `{"type":"input","id":"c1","lits":[1,-2]}`.
//! 3. One or more resolution steps:
//!    `{"type":"resolve","id":"r1","pivot":1,"left":"c1","right":"c2",
//!      "resolvent":[2]}`.
//! 4. Exactly one terminal root marker referencing the empty clause:
//!    `{"type":"root","ref":"r3"}`.
//!
//! This module is responsible only for *record shape* and *storage*. Whether a
//! step is logically legal is decided by the independent checker.

use std::collections::HashMap;

use serde::Deserialize;

use crate::syntax::Clause;

/// A single JSONL record as it appears on the wire.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type")]
pub enum RawRecord {
    #[serde(rename = "header")]
    Header { format_version: String },
    #[serde(rename = "input")]
    Input { id: String, lits: Vec<i64> },
    #[serde(rename = "resolve")]
    Resolve {
        id: String,
        pivot: i64,
        left: String,
        right: String,
        resolvent: Vec<i64>,
    },
    #[serde(rename = "root")]
    Root {
        #[serde(rename = "ref")]
        refer: String,
    },
}

/// How a stored clause node was introduced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NodeOrigin {
    Input,
    Derived {
        pivot: u32,
        left: String,
        right: String,
    },
}

/// A clause that has been accepted into the proof store.
#[derive(Debug, Clone)]
pub struct StoredNode {
    pub clause: Clause,
    pub origin: NodeOrigin,
    pub defined_at_line: u64,
}

/// Result of parsing one raw line.
pub enum ParsedLine {
    Record(RawRecord),
    /// The line was not valid JSON or did not match a known record shape.
    Malformed(String),
}

/// Parse one non-empty line into a record, separating shape errors from logic.
pub fn parse_line(line: &str) -> ParsedLine {
    match serde_json::from_str::<RawRecord>(line) {
        Ok(rec) => ParsedLine::Record(rec),
        Err(e) => ParsedLine::Malformed(e.to_string()),
    }
}

/// Append-only store of clause nodes keyed by their proof-local identifiers.
#[derive(Debug, Default)]
pub struct NodeStore {
    nodes: HashMap<String, StoredNode>,
}

impl NodeStore {
    pub fn new() -> Self {
        NodeStore {
            nodes: HashMap::new(),
        }
    }

    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    pub fn contains(&self, id: &str) -> bool {
        self.nodes.contains_key(id)
    }

    pub fn get(&self, id: &str) -> Option<&StoredNode> {
        self.nodes.get(id)
    }

    /// Insert a node. A redefinition (which also models a duplicated or
    /// ambiguous record) is rejected.
    pub fn insert(&mut self, id: String, node: StoredNode) -> Result<(), String> {
        if self.nodes.contains_key(&id) {
            return Err(id);
        }
        self.nodes.insert(id, node);
        Ok(())
    }
}
