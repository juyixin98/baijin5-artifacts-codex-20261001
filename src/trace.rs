//! Trace model, validation, fixture loading, and the seeded random generator.
//!
//! A trace is an ordered list of operations. Normalization sorts by
//! `(at_ns, kind)` with arrivals before cancels at equal timestamps (the
//! engine's documented tie-break). Validation rejects malformed input with
//! `ApiError::Validation` — duplicate request ids included, because identity
//! confusion would corrupt completion accounting.

use crate::error::ApiError;
use crate::request::{BlockRequest, Direction, RequestId};
use serde::Deserialize;
use std::collections::HashSet;
use std::path::Path;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TraceOp {
    Arrive(BlockRequest),
    Cancel { at_ns: u64, id: RequestId },
}

impl TraceOp {
    pub fn at_ns(&self) -> u64 {
        match self {
            TraceOp::Arrive(r) => r.arrival_ns,
            TraceOp::Cancel { at_ns, .. } => *at_ns,
        }
    }
    /// Sort order within one timestamp: arrivals (0) before cancels (1).
    fn order(&self) -> u8 {
        match self {
            TraceOp::Arrive(_) => 0,
            TraceOp::Cancel { .. } => 1,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Trace {
    pub name: String,
    pub ops: Vec<TraceOp>,
}

/// JSON form of one trace operation (fixtures and inline API traces).
#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum OpJson {
    Arrive {
        at_ns: u64,
        id: String,
        start: u64,
        len: u64,
        direction: Direction,
        #[serde(default)]
        deadline_ns: Option<u64>,
    },
    Cancel {
        at_ns: u64,
        id: String,
    },
}

pub fn parse_ops(name: &str, ops: Vec<OpJson>) -> Result<Trace, ApiError> {
    let mut out = Vec::with_capacity(ops.len());
    let mut ids: HashSet<&str> = HashSet::new();
    for op in &ops {
        match op {
            OpJson::Arrive {
                at_ns,
                id,
                start,
                len,
                direction,
                deadline_ns,
            } => {
                if id.is_empty() {
                    return Err(ApiError::Validation("request id must not be empty".into()));
                }
                if *len == 0 {
                    return Err(ApiError::Validation(format!(
                        "request {id}: len must be > 0"
                    )));
                }
                if start.checked_add(*len).is_none() {
                    return Err(ApiError::Validation(format!(
                        "request {id}: sector range [{start}, {start}+{len}) overflows u64"
                    )));
                }
                if !ids.insert(id.as_str()) {
                    return Err(ApiError::Validation(format!(
                        "duplicate request id {id}: request identities must be unique"
                    )));
                }
                out.push(TraceOp::Arrive(BlockRequest {
                    id: id.clone(),
                    start: *start,
                    len: *len,
                    direction: *direction,
                    arrival_ns: *at_ns,
                    deadline_ns: *deadline_ns,
                }));
            }
            OpJson::Cancel { at_ns, id } => {
                out.push(TraceOp::Cancel {
                    at_ns: *at_ns,
                    id: id.clone(),
                });
            }
        }
    }
    // Stable sort by (time, kind): arrivals before cancels at equal t.
    out.sort_by_key(|op| (op.at_ns(), op.order()));
    Ok(Trace {
        name: name.to_string(),
        ops: out,
    })
}

/// Load a named fixture trace from `<fixtures_dir>/<name>.json`.
pub fn load_fixture(fixtures_dir: &Path, name: &str) -> Result<Trace, ApiError> {
    if !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        return Err(ApiError::Validation(format!(
            "trace name {name:?} may only contain [A-Za-z0-9_-]"
        )));
    }
    let path = fixtures_dir.join(format!("{name}.json"));
    let text = std::fs::read_to_string(&path).map_err(|_| {
        ApiError::NotFound(format!(
            "trace {name:?} not found (looked for {})",
            path.display()
        ))
    })?;
    let ops: Vec<OpJson> = serde_json::from_str(&text)
        .map_err(|e| ApiError::Validation(format!("trace {name:?} is malformed: {e}")))?;
    parse_ops(name, ops)
}

/// Deterministic xorshift64* — no external rand dependency, fully seeded.
struct XorShift(u64);

impl XorShift {
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }
}

/// Seeded random trace: reproducible for any (seed, count). The generator is
/// only used for *input* generation; expected outputs in tests are either
/// hand-computed (tiny/merge/cancel fixtures) or scheduler-independent
/// invariants (conservation, monotonicity), never re-derived from the engine.
pub fn random_trace(seed: u64, count: usize) -> Trace {
    let mut rng = XorShift(seed.max(1));
    let mut ops = Vec::with_capacity(count);
    for i in 0..count {
        // Arrivals compressed into 100 us so a real queue forms and the
        // schedulers' choices actually diverge.
        let at = rng.next() % 100_000;
        let start = rng.next() % 4096;
        let len = 1 + rng.next() % 64;
        let direction = if rng.next() % 2 == 0 {
            Direction::Read
        } else {
            Direction::Write
        };
        ops.push(OpJson::Arrive {
            at_ns: at,
            id: format!("rand-{seed}-{i}"),
            start,
            len,
            direction,
            deadline_ns: None,
        });
    }
    // Ids are unique by construction; parse_ops also normalizes ordering.
    parse_ops(&format!("random:{seed}:{count}"), ops).expect("generated trace is valid")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_zero_len_duplicate_id_and_overflow() {
        let zero_len = vec![OpJson::Arrive {
            at_ns: 0,
            id: "a".into(),
            start: 0,
            len: 0,
            direction: Direction::Read,
            deadline_ns: None,
        }];
        assert!(matches!(
            parse_ops("t", zero_len),
            Err(ApiError::Validation(_))
        ));
        let dup = vec![
            OpJson::Arrive {
                at_ns: 0,
                id: "a".into(),
                start: 0,
                len: 1,
                direction: Direction::Read,
                deadline_ns: None,
            },
            OpJson::Arrive {
                at_ns: 1,
                id: "a".into(),
                start: 5,
                len: 1,
                direction: Direction::Read,
                deadline_ns: None,
            },
        ];
        assert!(matches!(parse_ops("t", dup), Err(ApiError::Validation(_))));
        let overflow = vec![OpJson::Arrive {
            at_ns: 0,
            id: "a".into(),
            start: u64::MAX,
            len: 1,
            direction: Direction::Read,
            deadline_ns: None,
        }];
        assert!(matches!(
            parse_ops("t", overflow),
            Err(ApiError::Validation(_))
        ));
    }

    #[test]
    fn sorts_arrivals_before_cancels_at_equal_time() {
        let ops = vec![
            OpJson::Cancel {
                at_ns: 0,
                id: "a".into(),
            },
            OpJson::Arrive {
                at_ns: 0,
                id: "a".into(),
                start: 0,
                len: 1,
                direction: Direction::Read,
                deadline_ns: None,
            },
        ];
        let t = parse_ops("t", ops).expect("valid");
        assert!(matches!(t.ops[0], TraceOp::Arrive(_)));
        assert!(matches!(t.ops[1], TraceOp::Cancel { .. }));
    }

    #[test]
    fn random_trace_is_deterministic_per_seed() {
        let a = random_trace(42, 32);
        let b = random_trace(42, 32);
        assert_eq!(a.ops, b.ops);
        let c = random_trace(43, 32);
        assert_ne!(a.ops, c.ops);
    }
}
