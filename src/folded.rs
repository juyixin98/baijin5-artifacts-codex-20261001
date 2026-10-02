//! Folded-stack generation (one line per distinct leaf path).

use serde::Serialize;

use crate::naming::Namer;
use crate::tree::CallTree;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct FoldedLine {
    /// Display names joined by ';', root first.
    pub stack: String,
    pub weight: f64,
}

/// Emits one line per tree node with non-zero self weight, sorted by stack
/// text for deterministic output.
pub fn fold(tree: &CallTree, namer: &Namer) -> Vec<FoldedLine> {
    let mut lines = Vec::new();
    let mut path: Vec<String> = Vec::new();
    fn walk(
        node: &crate::tree::TreeNode,
        namer: &Namer,
        path: &mut Vec<String>,
        out: &mut Vec<FoldedLine>,
    ) {
        path.push(namer.name(&node.frame.key));
        if node.self_weight > 0.0 {
            out.push(FoldedLine { stack: path.join(";"), weight: node.self_weight });
        }
        for child in node.children.values() {
            walk(child, namer, path, out);
        }
        path.pop();
    }
    for root in tree.roots.values() {
        walk(root, namer, &mut path, &mut lines);
    }
    lines.sort_by(|a, b| a.stack.cmp(&b.stack));
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stitch::{StitchEvidence, StitchedSample};
    use crate::symbols::{FrameKey, ResolvedFrame};
    use crate::tree::build_tree;

    fn frame(addr: u64, name: &str) -> ResolvedFrame {
        ResolvedFrame {
            key: FrameKey { module: "app".into(), addr },
            name: Some(name.into()),
        }
    }

    #[test]
    fn folded_lines_match_hand_computation() {
        let main = frame(0x1000, "main");
        let rec = frame(0x1001, "rec");
        let leaf = frame(0x1002, "leaf");
        let other = frame(0x1003, "other");
        let mk = |id: &str, weight: f64, frames: Vec<ResolvedFrame>| StitchedSample {
            sample_id: id.into(),
            weight,
            frames,
            evidence: StitchEvidence::Direct,
        };
        let samples = vec![
            mk("s1", 2.0, vec![main.clone(), rec.clone(), rec.clone(), leaf.clone()]),
            mk("s2", 1.0, vec![main.clone(), rec.clone(), leaf.clone()]),
            mk("s3", 4.0, vec![main.clone(), other.clone(), leaf.clone()]),
        ];
        let tree = build_tree(&samples).unwrap();
        let namer = Namer::build(tree.all_frames().iter());
        let lines = fold(&tree, &namer);
        assert_eq!(
            lines,
            vec![
                FoldedLine { stack: "main;other;leaf".into(), weight: 4.0 },
                FoldedLine { stack: "main;rec;leaf".into(), weight: 1.0 },
                FoldedLine { stack: "main;rec;rec;leaf".into(), weight: 2.0 },
            ]
        );
        let total: f64 = lines.iter().map(|l| l.weight).sum();
        assert_eq!(total, tree.total_weight, "folded weights conserve total");
    }
}
