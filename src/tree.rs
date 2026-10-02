//! Call-tree construction and path statistics.
//!
//! Semantics:
//! - The tree is a trie over full stack paths, so a recursive function
//!   appears once per *stack position* — `rec` at depth 1 and depth 2 are
//!   distinct nodes and are never merged.
//! - `self_weight` accumulates only on the node that is the sample's leaf.
//! - `inclusive_weight` accumulates on every node along the sample's path.
//! - Hot/cold paths walk from a root to a leaf following the max/min
//!   inclusive child; ties break on the smaller `(module, addr)` key so
//!   results are deterministic.

use std::collections::BTreeMap;

use crate::error::BackendError;
use crate::stitch::StitchedSample;
use crate::symbols::{FrameKey, ResolvedFrame};

#[derive(Debug, Clone)]
pub struct TreeNode {
    pub frame: ResolvedFrame,
    pub self_weight: f64,
    pub inclusive_weight: f64,
    /// Keyed by frame identity: recursion at different positions stays apart.
    pub children: BTreeMap<FrameKey, TreeNode>,
}

impl TreeNode {
    fn new(frame: ResolvedFrame) -> Self {
        Self { frame, self_weight: 0.0, inclusive_weight: 0.0, children: BTreeMap::new() }
    }
}

#[derive(Debug, Default)]
pub struct CallTree {
    pub roots: BTreeMap<FrameKey, TreeNode>,
    pub total_weight: f64,
    pub sample_count: usize,
}

fn checked_add(acc: f64, w: f64) -> Result<f64, BackendError> {
    let v = acc + w;
    if !v.is_finite() {
        return Err(BackendError::computation(
            "weight_overflow",
            "accumulated sample weight is not finite",
        ));
    }
    Ok(v)
}

fn insert(
    map: &mut BTreeMap<FrameKey, TreeNode>,
    frames: &[ResolvedFrame],
    w: f64,
) -> Result<(), BackendError> {
    let node = map
        .entry(frames[0].key.clone())
        .or_insert_with(|| TreeNode::new(frames[0].clone()));
    node.inclusive_weight = checked_add(node.inclusive_weight, w)?;
    if frames.len() == 1 {
        node.self_weight = checked_add(node.self_weight, w)?;
    } else {
        insert(&mut node.children, &frames[1..], w)?;
    }
    Ok(())
}

pub fn build_tree(samples: &[StitchedSample]) -> Result<CallTree, BackendError> {
    let mut tree = CallTree::default();
    for s in samples {
        tree.total_weight = checked_add(tree.total_weight, s.weight)?;
        tree.sample_count += 1;
        insert(&mut tree.roots, &s.frames, s.weight)?;
    }
    Ok(tree)
}

#[derive(Debug, Clone, PartialEq)]
pub struct PathSummary {
    pub frames: Vec<ResolvedFrame>,
    /// Inclusive weight of the terminal (leaf) node.
    pub weight: f64,
}

#[derive(Debug, Clone)]
pub struct FlatEntry {
    pub frame: ResolvedFrame,
    /// Self weight aggregated across all stack positions of this identity.
    pub self_weight: f64,
}

impl CallTree {
    /// Picks the child with the extreme inclusive weight; deterministic on
    /// ties because `BTreeMap` iterates in ascending key order and strict
    /// comparison keeps the first (smallest-key) candidate.
    fn walk(&self, want_max: bool) -> Option<PathSummary> {
        fn pick_extreme(
            map: &BTreeMap<FrameKey, TreeNode>,
            want_max: bool,
        ) -> Option<&TreeNode> {
            let mut best: Option<&TreeNode> = None;
            for node in map.values() {
                let better = match best {
                    None => true,
                    Some(b) if want_max => node.inclusive_weight > b.inclusive_weight,
                    Some(b) => node.inclusive_weight < b.inclusive_weight,
                };
                if better {
                    best = Some(node);
                }
            }
            best
        }
        let mut frames = Vec::new();
        let mut weight = 0.0;
        let mut current = pick_extreme(&self.roots, want_max);
        while let Some(node) = current {
            frames.push(node.frame.clone());
            weight = node.inclusive_weight;
            current = pick_extreme(&node.children, want_max);
        }
        if frames.is_empty() {
            None
        } else {
            Some(PathSummary { frames, weight })
        }
    }

    pub fn hot_path(&self) -> Option<PathSummary> {
        self.walk(true)
    }

    pub fn cold_path(&self) -> Option<PathSummary> {
        self.walk(false)
    }

    /// Flat profile: self weight per frame identity, positions merged.
    /// Sorted by self weight descending, ties by identity ascending.
    pub fn flat_profile(&self) -> Vec<FlatEntry> {
        let mut acc: BTreeMap<FrameKey, FlatEntry> = BTreeMap::new();
        fn walk(node: &TreeNode, acc: &mut BTreeMap<FrameKey, FlatEntry>) {
            acc.entry(node.frame.key.clone())
                .and_modify(|e| e.self_weight += node.self_weight)
                .or_insert_with(|| FlatEntry {
                    frame: node.frame.clone(),
                    self_weight: node.self_weight,
                });
            for child in node.children.values() {
                walk(child, acc);
            }
        }
        for root in self.roots.values() {
            walk(root, &mut acc);
        }
        let mut entries: Vec<FlatEntry> = acc.into_values().collect();
        entries.sort_by(|a, b| {
            b.self_weight
                .partial_cmp(&a.self_weight)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.frame.key.cmp(&b.frame.key))
        });
        entries
    }

    pub fn all_frames(&self) -> Vec<ResolvedFrame> {
        let mut out = Vec::new();
        fn walk(node: &TreeNode, out: &mut Vec<ResolvedFrame>) {
            out.push(node.frame.clone());
            for child in node.children.values() {
                walk(child, out);
            }
        }
        for root in self.roots.values() {
            walk(root, &mut out);
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stitch::StitchEvidence;

    fn frame(addr: u64, name: &str) -> ResolvedFrame {
        ResolvedFrame {
            key: FrameKey { module: "app".into(), addr },
            name: Some(name.into()),
        }
    }

    fn sample(id: &str, weight: f64, path: &[ResolvedFrame]) -> StitchedSample {
        StitchedSample {
            sample_id: id.into(),
            weight,
            frames: path.to_vec(),
            evidence: StitchEvidence::Direct,
        }
    }

    /// Hand-computed fixture (mirrors fixtures/recursion_shared_leaf.json):
    ///   s1: main;rec;rec;leaf  w=2
    ///   s2: main;rec;leaf      w=1
    ///   s3: main;other;leaf    w=4
    fn fixture() -> Vec<StitchedSample> {
        let main = frame(0x1000, "main");
        let rec = frame(0x1001, "rec");
        let leaf = frame(0x1002, "leaf");
        let other = frame(0x1003, "other");
        vec![
            sample("s1", 2.0, &[main.clone(), rec.clone(), rec.clone(), leaf.clone()]),
            sample("s2", 1.0, &[main.clone(), rec.clone(), leaf.clone()]),
            sample("s3", 4.0, &[main.clone(), other.clone(), leaf.clone()]),
        ]
    }

    #[test]
    fn recursion_is_preserved_by_stack_position() {
        let tree = build_tree(&fixture()).unwrap();
        let main = &tree.roots[&frame(0x1000, "main").key];
        assert_eq!(main.inclusive_weight, 7.0);
        assert_eq!(main.self_weight, 0.0);

        let rec_d1 = &main.children[&frame(0x1001, "rec").key];
        assert_eq!(rec_d1.inclusive_weight, 3.0, "rec at depth 1 sees s1+s2");
        // rec at depth 2 is a *child* of rec at depth 1, not the same node.
        let rec_d2 = &rec_d1.children[&frame(0x1001, "rec").key];
        assert_eq!(rec_d2.inclusive_weight, 2.0, "rec at depth 2 sees only s1");
        assert_eq!(rec_d2.children[&frame(0x1002, "leaf").key].self_weight, 2.0);
        // s2's leaf hangs directly off depth-1 rec.
        assert_eq!(rec_d1.children[&frame(0x1002, "leaf").key].self_weight, 1.0);
    }

    #[test]
    fn shared_leaf_accumulates_self_per_path() {
        let tree = build_tree(&fixture()).unwrap();
        let main = &tree.roots[&frame(0x1000, "main").key];
        let other = &main.children[&frame(0x1003, "other").key];
        assert_eq!(other.inclusive_weight, 4.0);
        assert_eq!(other.children[&frame(0x1002, "leaf").key].self_weight, 4.0);
        // Flat profile merges positions: leaf self = 2 + 1 + 4.
        let flat = tree.flat_profile();
        let leaf = flat.iter().find(|e| e.frame.key.addr == 0x1002).unwrap();
        assert_eq!(leaf.self_weight, 7.0);
        assert_eq!(flat[0].frame.key.addr, 0x1002, "leaf is the hottest by self");
    }

    #[test]
    fn hot_and_cold_paths_match_hand_computation() {
        let tree = build_tree(&fixture()).unwrap();
        let hot = tree.hot_path().unwrap();
        let names: Vec<&str> = hot.frames.iter().map(|f| f.name.as_deref().unwrap()).collect();
        assert_eq!(names, ["main", "other", "leaf"]);
        assert_eq!(hot.weight, 4.0);

        let cold = tree.cold_path().unwrap();
        let names: Vec<&str> = cold.frames.iter().map(|f| f.name.as_deref().unwrap()).collect();
        assert_eq!(names, ["main", "rec", "leaf"]);
        assert_eq!(cold.weight, 1.0);
    }

    #[test]
    fn total_weight_is_sum_of_sample_weights() {
        let tree = build_tree(&fixture()).unwrap();
        assert_eq!(tree.total_weight, 7.0);
        assert_eq!(tree.sample_count, 3);
    }

    #[test]
    fn non_finite_accumulation_is_computation_error() {
        let main = frame(0x1000, "main");
        let samples = vec![
            sample("a", f64::MAX, std::slice::from_ref(&main)),
            sample("b", f64::MAX, std::slice::from_ref(&main)),
        ];
        let err = build_tree(&samples).unwrap_err();
        assert_eq!(err.category, crate::error::ErrorCategory::Computation);
        assert_eq!(err.code, "weight_overflow");
    }
}
