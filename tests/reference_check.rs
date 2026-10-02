//! Cross-check against an independent reference implementation.
//!
//! The reference below is deliberately naive (O(samples x depth^2) prefix
//! walks over plain string paths) and shares no code with the call-tree
//! under test. Literal expectations in `api_integration.rs` remain the
//! primary oracle; this test guards against both drifting together.

use std::collections::BTreeMap;

use stackstats::model::SampleInput;
use stackstats::stitch::stitch_samples;
use stackstats::symbols::SymbolTable;
use stackstats::tree::build_tree;

/// Reference accumulator: per-sample path accumulation over display paths.
struct Reference {
    /// (path prefix as joined "addr,addr,...") -> inclusive weight.
    inclusive: BTreeMap<String, f64>,
    /// full path -> self weight.
    self_: BTreeMap<String, f64>,
    total: f64,
}

impl Reference {
    fn accumulate(samples: &[SampleInput], table: &SymbolTable) -> Self {
        let stitched = stitch_samples(samples, table).unwrap();
        let mut r = Reference { inclusive: BTreeMap::new(), self_: BTreeMap::new(), total: 0.0 };
        for s in &stitched {
            r.total += s.weight;
            let addrs: Vec<u64> = s.frames.iter().map(|f| f.key.addr).collect();
            for i in 1..=addrs.len() {
                let prefix = addrs[..i]
                    .iter()
                    .map(|a| a.to_string())
                    .collect::<Vec<_>>()
                    .join(",");
                *r.inclusive.entry(prefix.clone()).or_insert(0.0) += s.weight;
                if i == addrs.len() {
                    *r.self_.entry(prefix).or_insert(0.0) += s.weight;
                }
            }
        }
        r
    }
}

/// Walks the real tree and flattens it into the same shape as `Reference`.
fn flatten_tree(tree: &stackstats::tree::CallTree) -> Reference {
    let mut r = Reference { inclusive: BTreeMap::new(), self_: BTreeMap::new(), total: tree.total_weight };
    fn walk(
        node: &stackstats::tree::TreeNode,
        prefix: &mut Vec<u64>,
        r: &mut Reference,
    ) {
        prefix.push(node.frame.key.addr);
        let key = prefix.iter().map(|a| a.to_string()).collect::<Vec<_>>().join(",");
        r.inclusive.insert(key.clone(), node.inclusive_weight);
        if node.self_weight > 0.0 {
            r.self_.insert(key, node.self_weight);
        }
        for child in node.children.values() {
            walk(child, prefix, r);
        }
        prefix.pop();
    }
    for root in tree.roots.values() {
        walk(root, &mut Vec::new(), &mut r);
    }
    r
}

fn fixture_samples(name: &str) -> (Vec<SampleInput>, SymbolTable) {
    let path = format!("{}/fixtures/{name}", env!("CARGO_MANIFEST_DIR"));
    let text = std::fs::read_to_string(&path).unwrap();
    let doc: serde_json::Value = serde_json::from_str(&text).unwrap();
    let symbols = serde_json::from_value(doc["run"]["symbols"].clone()).unwrap();
    let samples = serde_json::from_value(doc["samples"].clone()).unwrap();
    (samples, SymbolTable::build(symbols).unwrap())
}

fn assert_matches_reference(fixture: &str) {
    let (samples, table) = fixture_samples(fixture);
    let reference = Reference::accumulate(&samples, &table);
    let stitched = stitch_samples(&samples, &table).unwrap();
    let tree = build_tree(&stitched).unwrap();
    let actual = flatten_tree(&tree);

    assert_eq!(actual.total, reference.total, "{fixture}: total weight diverged");
    assert_eq!(
        actual.inclusive, reference.inclusive,
        "{fixture}: per-prefix inclusive weights diverged"
    );
    assert_eq!(actual.self_, reference.self_, "{fixture}: per-path self weights diverged");
    eprintln!(
        "[test_log] fixture={fixture} prefixes={} self_paths={} total_weight={} verdict=match",
        reference.inclusive.len(),
        reference.self_.len(),
        reference.total
    );
}

#[test]
fn recursion_fixture_matches_reference() {
    assert_matches_reference("recursion_shared_leaf.json");
}

#[test]
fn async_fixture_matches_reference() {
    assert_matches_reference("async_fragments.json");
}

#[test]
fn weighted_fixture_matches_reference() {
    assert_matches_reference("weighted_dropped.json");
}

#[test]
fn missing_symbols_fixture_matches_reference() {
    assert_matches_reference("missing_symbols.json");
}
