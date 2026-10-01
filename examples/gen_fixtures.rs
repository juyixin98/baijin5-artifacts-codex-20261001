//! Local synthetic fixture generator (Rust only; no external services).
//!
//! Writes a small finite model and a family of formulas under `fixtures/gen`.
//! Run with: cargo run --example gen_fixtures
//!
//! The generated model uses sorts of deliberately different sizes (including an
//! empty sort) so alternating quantifiers and empty-domain policy are exercised
//! without any real business data.

use std::fs;
use std::path::PathBuf;

use serde_json::json;

fn main() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let dir = root.join("fixtures/gen");
    fs::create_dir_all(dir.join("model")).unwrap();
    fs::create_dir_all(dir.join("formulas")).unwrap();

    let model = json!({
        "sorts": [
            { "name": "D", "elements": ["d0", "d1", "d2", "d3"] },
            { "name": "S", "elements": ["s0", "s1"] },
            { "name": "Empty", "elements": [] }
        ],
        "constants": [
            { "name": "zero", "sort": "D", "value": "d0" },
            { "name": "one", "sort": "D", "value": "d1" }
        ],
        "functions": [
            {
                "name": "succ",
                "param_sorts": ["D"],
                "result_sort": "D",
                "table": {
                    "d0": "d1", "d1": "d2", "d2": "d3", "d3": "d0"
                }
            }
        ],
        "predicates": [
            {
                "name": "edge",
                "param_sorts": ["D", "S"],
                "facts": [["d0", "s0"], ["d2", "s1"], ["d3", "s0"]]
            },
            {
                "name": "onEmpty",
                "param_sorts": ["Empty"],
                "facts": []
            }
        ]
    });
    fs::write(
        dir.join("model/gen_model.json"),
        serde_json::to_string_pretty(&model).unwrap(),
    )
    .unwrap();

    let formulas: &[(&str, serde_json::Value)] = &[
        (
            "g01_forall_exists.json",
            json!({"op":"forall","var":"d","sort":"D","inner":{
                "op":"exists","var":"s","sort":"S","inner":{
                    "op":"pred","name":"edge","args":[
                        {"t":"var","name":"d"},{"t":"var","name":"s"}]}}}),
        ),
        (
            "g02_shadow.json",
            json!({"op":"forall","var":"d","sort":"D","inner":{
                "op":"or","children":[
                    {"op":"pred","name":"edge","args":[
                        {"t":"var","name":"d"},{"t":"elem","value":"s0"}]},
                    {"op":"exists","var":"d","sort":"D","inner":{
                        "op":"eq","left":{"t":"var","name":"d"},
                        "right":{"t":"elem","value":"d0"}}}]}}),
        ),
        (
            "g03_empty.json",
            json!({"op":"forall","var":"e","sort":"Empty","inner":{
                "op":"pred","name":"onEmpty","args":[{"t":"var","name":"e"}]}}),
        ),
        (
            "g04_succ_cycle.json",
            json!({"op":"forall","var":"d","sort":"D","inner":{
                "op":"eq",
                "left":{"t":"app","name":"succ","args":[
                    {"t":"app","name":"succ","args":[
                        {"t":"app","name":"succ","args":[
                            {"t":"app","name":"succ","args":[
                                {"t":"var","name":"d"}]}]}]}]},
                "right":{"t":"var","name":"d"}}}),
        ),
    ];

    for (name, value) in formulas {
        fs::write(
            dir.join("formulas").join(name),
            serde_json::to_string_pretty(value).unwrap(),
        )
        .unwrap();
    }

    println!(
        "generated {} formulas and 1 model under {}",
        formulas.len(),
        dir.display()
    );
}
