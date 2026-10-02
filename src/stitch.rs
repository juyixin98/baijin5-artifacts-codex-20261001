//! Async stack stitching with explicit correlation evidence.
//!
//! A sample whose `async_link.parent_token` matches another sample's
//! `token` in the same run is a continuation: its frames are appended to
//! the parent's full path. The token match is the *only* accepted evidence;
//! it is recorded per sample so every stitched path is auditable.
//!
//! - No matching token → the fragment keeps its own root under a synthetic
//!   `async_detached(task=N)` frame. Detached fragments are never merged
//!   into unrelated roots.
//! - Duplicate tokens or parent cycles are input errors: the correlation
//!   data itself is contradictory.

use std::collections::HashMap;

use serde::Serialize;

use crate::error::BackendError;
use crate::model::SampleInput;
use crate::symbols::{FrameKey, ResolvedFrame, SymbolTable};

/// Module used for synthetic stitching frames; addresses sort after all
/// real frames so synthetic roots never win hot-path tie-breaks.
pub const ASYNC_MODULE: &str = "[async]";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum StitchEvidence {
    /// Synchronous sample, no async metadata.
    Direct,
    /// Attached to parent sample via matching token.
    Stitched { parent_sample_id: String, token: String },
    /// Claimed a parent token that no sample in this run publishes.
    Detached { task_id: u64, parent_token: String },
}

#[derive(Debug, Clone, Serialize)]
pub struct StitchedSample {
    pub sample_id: String,
    pub weight: f64,
    /// Full root-first path after stitching.
    pub frames: Vec<ResolvedFrame>,
    pub evidence: StitchEvidence,
}

fn detached_root(task_id: u64) -> ResolvedFrame {
    ResolvedFrame {
        key: FrameKey { module: ASYNC_MODULE.into(), addr: u64::MAX - task_id },
        name: Some(format!("async_detached(task={task_id})")),
    }
}

/// Resolves symbols and stitches async fragments for one run.
///
/// `inputs` must already be validated (`SampleInput::validate`).
pub fn stitch_samples(
    inputs: &[SampleInput],
    table: &SymbolTable,
) -> Result<Vec<StitchedSample>, BackendError> {
    // Index published tokens; a token claimed twice is contradictory input.
    let mut token_owner: HashMap<&str, usize> = HashMap::new();
    for (i, s) in inputs.iter().enumerate() {
        if let Some(link) = &s.async_link {
            if let Some(tok) = &link.token {
                if let Some(prev) = token_owner.insert(tok.as_str(), i) {
                    return Err(BackendError::input(
                        "async_duplicate_token",
                        format!(
                            "token {:?} published by both {} and {}",
                            tok, inputs[prev].sample_id, s.sample_id
                        ),
                    ));
                }
            }
        }
    }

    // Parent pointer per sample (None when no link or token unmatched).
    let parent: Vec<Option<usize>> = inputs
        .iter()
        .map(|s| {
            s.async_link
                .as_ref()
                .and_then(|l| l.parent_token.as_ref())
                .and_then(|t| token_owner.get(t.as_str()).copied())
        })
        .collect();

    // Cycle detection over the functional parent graph.
    detect_cycle(inputs, &parent)?;

    // Full paths, memoized (a parent chain is resolved once).
    let mut memo: Vec<Option<Vec<ResolvedFrame>>> = vec![None; inputs.len()];
    let mut out = Vec::with_capacity(inputs.len());
    for i in 0..inputs.len() {
        let frames = full_path(i, inputs, &parent, table, &mut memo);
        let evidence = match &inputs[i].async_link {
            None => StitchEvidence::Direct,
            Some(link) => match (parent[i], &link.parent_token) {
                (Some(p), Some(tok)) => StitchEvidence::Stitched {
                    parent_sample_id: inputs[p].sample_id.clone(),
                    token: tok.clone(),
                },
                (None, Some(tok)) => {
                    StitchEvidence::Detached { task_id: link.task_id, parent_token: tok.clone() }
                }
                // Publishes a token but continues nothing itself.
                _ => StitchEvidence::Direct,
            },
        };
        out.push(StitchedSample {
            sample_id: inputs[i].sample_id.clone(),
            weight: inputs[i].weight,
            frames,
            evidence,
        });
    }
    Ok(out)
}

fn detect_cycle(inputs: &[SampleInput], parent: &[Option<usize>]) -> Result<(), BackendError> {
    const IN_STACK: u8 = 1;
    const DONE: u8 = 2;
    let mut state = vec![0u8; inputs.len()];
    for start in 0..inputs.len() {
        if state[start] != 0 {
            continue;
        }
        let mut chain = Vec::new();
        let mut cur = start;
        loop {
            if state[cur] == IN_STACK {
                return Err(BackendError::input(
                    "async_cycle",
                    format!("async parent chain cycles at sample {}", inputs[cur].sample_id),
                ));
            }
            if state[cur] == DONE {
                break;
            }
            state[cur] = IN_STACK;
            chain.push(cur);
            match parent[cur] {
                Some(p) => cur = p,
                None => break,
            }
        }
        for n in chain {
            state[n] = DONE;
        }
    }
    Ok(())
}

fn full_path(
    i: usize,
    inputs: &[SampleInput],
    parent: &[Option<usize>],
    table: &SymbolTable,
    memo: &mut [Option<Vec<ResolvedFrame>>],
) -> Vec<ResolvedFrame> {
    if let Some(cached) = &memo[i] {
        return cached.clone();
    }
    let mut path = match parent[i] {
        Some(p) => full_path(p, inputs, parent, table, memo),
        None => match &inputs[i].async_link {
            // Detached fragment: anchor under its own synthetic root.
            Some(link) if link.parent_token.is_some() => vec![detached_root(link.task_id)],
            _ => Vec::new(),
        },
    };
    path.extend(inputs[i].frames.iter().map(|f| table.resolve(f)));
    memo[i] = Some(path.clone());
    path
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{AsyncLink, FrameInput};

    fn table() -> SymbolTable {
        SymbolTable::build(vec![]).unwrap()
    }

    fn sync_sample(id: &str, addrs: &[u64]) -> SampleInput {
        SampleInput {
            sample_id: id.into(),
            weight: 1.0,
            frames: addrs.iter().map(|&addr| FrameInput { addr, module: None }).collect(),
            async_link: None,
        }
    }

    fn async_sample(
        id: &str,
        addrs: &[u64],
        task: u64,
        token: Option<&str>,
        parent_token: Option<&str>,
    ) -> SampleInput {
        SampleInput {
            async_link: Some(AsyncLink {
                task_id: task,
                token: token.map(|t| t.into()),
                parent_token: parent_token.map(|t| t.into()),
            }),
            ..sync_sample(id, addrs)
        }
    }

    fn addrs(s: &StitchedSample) -> Vec<u64> {
        s.frames.iter().map(|f| f.key.addr).collect()
    }

    #[test]
    fn child_path_extends_parent_with_evidence() {
        let inputs = vec![
            async_sample("a1", &[0x1000, 0x1010], 7, Some("tokA"), None),
            async_sample("a2", &[0x2000, 0x2001], 7, None, Some("tokA")),
        ];
        let out = stitch_samples(&inputs, &table()).unwrap();
        assert_eq!(addrs(&out[0]), vec![0x1000, 0x1010]);
        assert_eq!(out[0].evidence, StitchEvidence::Direct);
        assert_eq!(addrs(&out[1]), vec![0x1000, 0x1010, 0x2000, 0x2001]);
        assert_eq!(
            out[1].evidence,
            StitchEvidence::Stitched { parent_sample_id: "a1".into(), token: "tokA".into() }
        );
    }

    #[test]
    fn unmatched_parent_token_detaches_under_synthetic_root() {
        let inputs = vec![async_sample("a3", &[0x3000], 9, None, Some("tokMissing"))];
        let out = stitch_samples(&inputs, &table()).unwrap();
        assert_eq!(
            out[0].evidence,
            StitchEvidence::Detached { task_id: 9, parent_token: "tokMissing".into() }
        );
        assert_eq!(out[0].frames[0].key.module, ASYNC_MODULE);
        assert_eq!(out[0].frames[1].key.addr, 0x3000);
    }

    #[test]
    fn chained_fragments_stitch_transitively() {
        let inputs = vec![
            async_sample("a", &[1], 1, Some("t1"), None),
            async_sample("b", &[2], 1, Some("t2"), Some("t1")),
            async_sample("c", &[3], 1, None, Some("t2")),
        ];
        let out = stitch_samples(&inputs, &table()).unwrap();
        assert_eq!(addrs(&out[2]), vec![1, 2, 3]);
    }

    #[test]
    fn duplicate_token_is_input_error() {
        let inputs = vec![
            async_sample("a", &[1], 1, Some("dup"), None),
            async_sample("b", &[2], 1, Some("dup"), None),
        ];
        let err = stitch_samples(&inputs, &table()).unwrap_err();
        assert_eq!(err.category, crate::error::ErrorCategory::Input);
        assert_eq!(err.code, "async_duplicate_token");
    }

    #[test]
    fn parent_cycle_is_input_error() {
        let inputs = vec![
            async_sample("a", &[1], 1, Some("ta"), Some("tb")),
            async_sample("b", &[2], 1, Some("tb"), Some("ta")),
        ];
        let err = stitch_samples(&inputs, &table()).unwrap_err();
        assert_eq!(err.category, crate::error::ErrorCategory::Input);
        assert_eq!(err.code, "async_cycle");
    }
}
