//! Hand-computed fixtures: even sample, all-NULL group, equal-frequency mode
//! tie, and a large repeated group. Expected numbers are derived by hand in
//! each assertion, then independently re-derived by the reference evaluator.

mod common;

use common::{find_group, reference, result_status, result_value, run_to_json, test_config};
use serde_json::{json, Value};

const HAND_FIXTURE: &str = include_str!("data/hand_computed.json");

#[test]
fn hand_computed_even_null_tie_repeat() {
    let (cfg, _td) = test_config();
    let req: Value = serde_json::from_str(HAND_FIXTURE).unwrap();
    let resp = run_to_json(req.clone(), &cfg);
    assert_eq!(resp["status"], "complete");

    // ---- Group A: [10,20,30,40] even sample ---------------------------------
    let a = find_group(&resp, json!("A"));
    assert_eq!(a["rows"], 4);
    // op0 cont i64->f64 p=.5: (20+30)/2 = 25
    assert_eq!(result_status(a, 0), "ok");
    assert_eq!(result_value(a, 0), json!(25.0));
    // op1 disc p=.5: rank ceil(2)=2 -> 20
    assert_eq!(result_value(a, 1), json!(20));
    // op2 cont f64 p=.5: 25
    assert_eq!(result_value(a, 2), json!(25.0));
    // op3 mode v: all four distinct once -> smallest 10, tie true, freq 1
    assert_eq!(result_value(a, 3), json!(10));
    assert_eq!(a["results"][3]["tie"], json!(true));
    assert_eq!(a["results"][3]["frequency"], json!(1));
    // op4 mode s: apple twice -> winner apple, no tie, freq 2
    assert_eq!(result_value(a, 4), json!("apple"));
    assert_eq!(a["results"][4]["tie"], json!(false));
    assert_eq!(a["results"][4]["frequency"], json!(2));
    // op5 string_agg s asc with '-' : apple-apple-banana-cherry
    assert_eq!(result_value(a, 5), json!("apple-apple-banana-cherry"));

    // ---- Group B: measure entirely NULL -------------------------------------
    let b = find_group(&resp, json!("B"));
    assert_eq!(b["rows"], 3, "rows count includes NULL-measure rows");
    for op in 0..6 {
        assert_eq!(result_status(b, op), "ok", "op {op}");
        assert!(
            result_value(b, op).is_null(),
            "all-NULL -> SQL NULL for op {op}"
        );
        assert_eq!(b["results"][op]["non_null"], json!(0));
    }

    // ---- Group C: equal-frequency mode tie [5,5,7,7] ------------------------
    let c = find_group(&resp, json!("C"));
    assert_eq!(result_value(c, 3), json!(5), "smallest key wins tie");
    assert_eq!(c["results"][3]["tie"], json!(true));
    assert_eq!(c["results"][3]["frequency"], json!(2));
    assert_eq!(result_value(c, 4), json!("x"));
    assert_eq!(c["results"][4]["tie"], json!(true));

    // ---- Group D: large repeated group (six 9s / six z) ---------------------
    let d = find_group(&resp, json!("D"));
    assert_eq!(d["rows"], 6);
    assert_eq!(result_value(d, 0), json!(9.0));
    assert_eq!(result_value(d, 1), json!(9));
    assert_eq!(result_value(d, 3), json!(9));
    assert_eq!(d["results"][3]["frequency"], json!(6));
    assert_eq!(d["results"][3]["tie"], json!(false));
    assert_eq!(result_value(d, 5), json!("z-z-z-z-z-z"));

    // ---- Independent reference cross-check (every group, every op) ----------
    let expected = reference::evaluate(&req);
    for (_key, want) in expected {
        let got = find_group(&resp, want["group"].clone());
        assert_eq!(got["rows"], want["rows"], "rows for {}", want["group"]);
        for (op, w) in want["results"].as_array().unwrap().iter().enumerate() {
            assert_eq!(
                result_value(got, op),
                w["value"],
                "value mismatch group={} op={op}",
                want["group"]
            );
            assert_eq!(got["results"][op]["non_null"], w["non_null"]);
            if w.get("tie").is_some() {
                assert_eq!(got["results"][op]["tie"], w["tie"]);
            }
        }
    }
}

#[test]
fn null_filtering_does_not_occupy_ranks() {
    // [1, NULL, 2, NULL, 3]: N=3 non-null; cont p=.5 -> h=1 -> exactly 2.
    let (cfg, _td) = test_config();
    let req = json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [1,1,1,1,1] },
            { "name": "v", "data_type": "f64", "values": [1.0, null, 2.0, null, 3.0] }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": 0.5, "method": "continuous" },
            { "op": "percentile", "column": "v", "p": 0.5, "method": "discrete" }
        ]
    });
    let resp = run_to_json(req, &cfg);
    let g = find_group(&resp, json!(1));
    assert_eq!(g["rows"], 5);
    assert_eq!(result_value(g, 0), json!(2.0));
    assert_eq!(result_value(g, 1), json!(2.0));
    assert_eq!(g["results"][0]["non_null"], json!(3));
}

#[test]
fn stable_order_with_equal_values() {
    // Equal values are many; string_agg must follow stable ordinal order for
    // ties. Values: b,a,b,a -> sorted asc -> a,a,b,b with the two a's and two
    // b's emitted in ingest order (identical strings, so result is a,a,b,b).
    let (cfg, _td) = test_config();
    let req = json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [0,0,0,0] },
            { "name": "s", "data_type": "utf8", "values": ["b","a","b","a"] }
        ],
        "operators": [
            { "op": "string_agg", "column": "s", "delimiter": ",", "order": "asc" },
            { "op": "string_agg", "column": "s", "delimiter": ",", "order": "desc" }
        ]
    });
    let resp = run_to_json(req, &cfg);
    let g = find_group(&resp, json!(0));
    assert_eq!(result_value(g, 0), json!("a,a,b,b"));
    assert_eq!(result_value(g, 1), json!("b,b,a,a"));
}
