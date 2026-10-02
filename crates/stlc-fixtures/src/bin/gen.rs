//! Deterministic fixture generator for scale cases.
//!
//! It writes only the *input* terms. Expected results are fixed by closed-form
//! facts used directly in the tests:
//!   * `id^(n) true` (left-associated identity redexes) takes exactly n
//!     beta steps to reach `true`, all at type Bool;
//!   * `lam x1 ... x1000. x1000` is already a normal form (0 steps) and
//!     stresses nesting depth;
//!   * truncating the run at budget b yields `budget_exhausted`.
//! No result here is produced by the system under test.

use std::fs;
use std::path::PathBuf;

fn out_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/generated")
}

fn write_case(name: &str, json: String) {
    let dir = out_dir();
    fs::create_dir_all(&dir).expect("create generated fixture dir");
    let path = dir.join(name);
    fs::write(&path, format!("{json}\n")).expect("write generated fixture");
    println!("generated {}", path.display());
}

fn id_chain_true(redexes: usize) -> String {
    let id = "(lam x: Bool. x)";
    let mut s = "true".to_string();
    for _ in 0..redexes {
        s = format!("{id} ({s})");
    }
    s
}

fn main() {
    // 13: 10 000 well-typed redexes.
    let ten_k = id_chain_true(10_000);
    write_case(
        "13_ten_thousand_redexes.json",
        serde_json::json!({
            "id": "F13-ten-thousand-redexes",
            "description": "10,000 left-associated identity redexes applied to true.",
            "manual_reasoning": "Each (\\x:Bool.x) contraction removes one wrapper; left-associated innermost order contracts exactly one per wrapper, so exactly 10,000 steps yield true. Budget 200,000 is sufficient.",
            "term_source": ten_k,
            "free_signature": [],
            "budget": 200_000,
            "expected": {
                "verdict": "ok",
                "normal_form": "true",
                "steps": 10_000,
                "free_vars": [],
                "result_type": "Bool"
            }
        })
        .to_string(),
    );

    // 14: same chain, budget deliberately stops it early.
    let thirty_two = id_chain_true(32);
    write_case(
        "14_budget_stop.json",
        serde_json::json!({
            "id": "F14-budget-stop",
            "description": "32 identity redexes with a budget of 8 must stop as budget_exhausted.",
            "manual_reasoning": "Terminating term, but only 8 of 32 contractions fit the budget; budget exhaustion is a separate category from type errors and leaves a non-normal residual.",
            "term_source": thirty_two,
            "free_signature": [],
            "budget": 8,
            "expected": {
                "verdict": "budget_exhausted"
            }
        })
        .to_string(),
    );

    // 15: deeply nested, already-normal abstraction chain.
    let depth = 1_000usize;
    let mut body = String::from("lam x0: Bool. x0");
    for i in 1..depth {
        body = format!("lam x{i}: Bool. ({body})");
    }
    write_case(
        "15_deep_nested_normal.json",
        serde_json::json!({
            "id": "F15-deep-nested-normal",
            "description": "1,000-deep nested abstractions selecting the innermost variable.",
            "manual_reasoning": "No applications occur, so the term is in normal form: 0 steps. The result is a 1,000-fold arrow ending in Bool; free vars {}. Stresses parser/recursion depth.",
            "term_source": body,
            "free_signature": [],
            "budget": 4096,
            "expected": {
                "verdict": "ok",
                "steps": 0,
                "free_vars": []
            }
        })
        .to_string(),
    );
}
