//! Validation-entry tests: quantile bounds and shape errors are rejected
//! *before* execution, each asserting the concrete failure category and code.

mod common;

use common::test_config;
use pctl::diagnostics::RequestId;
use pctl::error::ErrorKind;
use pctl::exec::execute_request;
use pctl::resources::CancellationToken;
use serde_json::json;

fn run_expect_error(req: serde_json::Value) -> pctl::error::PctlError {
    let parsed: pctl::spec::QueryRequest = serde_json::from_value(req).unwrap();
    let (cfg, _td) = test_config();
    let rid = RequestId::new();
    execute_request(&parsed, &rid, &cfg, CancellationToken::new())
        .expect_err("request must be rejected")
}

#[test]
fn rejects_quantile_above_one() {
    let e = run_expect_error(json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [1] },
            { "name": "v", "data_type": "i64", "values": [1] }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": 1.0001, "method": "continuous" }
        ]
    }));
    assert_eq!(e.kind, ErrorKind::Validation);
    assert_eq!(e.code, "quantile_out_of_range");
    assert_eq!(e.field.as_deref(), Some("operators[0].p"));
}

#[test]
fn rejects_negative_quantile() {
    let e = run_expect_error(json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [1] },
            { "name": "v", "data_type": "i64", "values": [1] }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": -0.2, "method": "discrete" }
        ]
    }));
    assert_eq!(e.kind, ErrorKind::Validation);
    assert_eq!(e.code, "quantile_out_of_range");
}

#[test]
fn rejects_nan_and_infinite_quantile() {
    // JSON cannot carry NaN/Infinity, so construct the typed request directly.
    use pctl::spec::{InputColumn, LogicalType, OperatorSpec, PercentileMethod, QueryRequest};

    fn request_with_p(p: f64) -> QueryRequest {
        QueryRequest {
            group_by: "g".into(),
            columns: vec![
                InputColumn {
                    name: "g".into(),
                    data_type: LogicalType::I64,
                    values: vec![json!(1)],
                },
                InputColumn {
                    name: "v".into(),
                    data_type: LogicalType::F64,
                    values: vec![json!(1.0)],
                },
            ],
            operators: vec![OperatorSpec::Percentile {
                column: "v".into(),
                p,
                method: PercentileMethod::Continuous,
            }],
            hints: Default::default(),
        }
    }

    for p in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let (cfg, _td) = test_config();
        let rid = RequestId::new();
        let e = execute_request(&request_with_p(p), &rid, &cfg, CancellationToken::new())
            .expect_err("non-finite quantile must be rejected");
        assert_eq!(e.kind, ErrorKind::Validation);
        assert_eq!(e.code, "quantile_not_finite", "p={p}");
    }
}

#[test]
fn boundary_quantiles_zero_and_one_are_accepted() {
    let (cfg, _td) = test_config();
    let req = json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [0, 0, 0] },
            { "name": "v", "data_type": "i64", "values": [5, 1, 9] }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": 0.0, "method": "continuous" },
            { "op": "percentile", "column": "v", "p": 1.0, "method": "continuous" }
        ]
    });
    let parsed = serde_json::from_value(req).unwrap();
    let rid = RequestId::new();
    let outcome = execute_request(&parsed, &rid, &cfg, CancellationToken::new()).unwrap();
    let groups = match outcome {
        pctl::exec::ExecOutcome::Complete(g, _) => g,
        other => panic!("{other:?}"),
    };
    let r = &groups[0].results;
    assert_eq!(serde_json::to_value(&r[0]).unwrap()["value"], json!(1.0));
    assert_eq!(serde_json::to_value(&r[1]).unwrap()["value"], json!(9.0));
}

#[test]
fn rejects_unknown_column_and_bad_type() {
    let e = run_expect_error(json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [1] },
            { "name": "s", "data_type": "utf8", "values": ["x"] }
        ],
        "operators": [
            { "op": "percentile", "column": "missing", "p": 0.5, "method": "continuous" }
        ]
    }));
    assert_eq!(e.kind, ErrorKind::Validation);
    assert_eq!(e.code, "unknown_column");

    let e = run_expect_error(json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [1] },
            { "name": "s", "data_type": "utf8", "values": ["x"] }
        ],
        "operators": [ { "op": "percentile", "column": "s", "p": 0.5,
                         "method": "continuous" } ]
    }));
    assert_eq!(e.code, "operator_type_mismatch");
    assert_eq!(e.kind, ErrorKind::Validation);

    let e = run_expect_error(json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [1] },
            { "name": "s", "data_type": "utf8", "values": ["x"] }
        ],
        "operators": [ { "op": "string_agg", "column": "s", "delimiter": "" } ]
    }));
    assert_eq!(e.code, "empty_delimiter");
}

#[test]
fn rejects_cell_type_mismatch_and_ragged_columns() {
    let parsed: Result<pctl::spec::QueryRequest, _> = serde_json::from_value(json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [1, 2] },
            { "name": "v", "data_type": "i64", "values": [1, "oops"] }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": 0.5, "method": "continuous" }
        ]
    }));
    let req = parsed.unwrap();
    let (cfg, _td) = test_config();
    let e = execute_request(&req, &RequestId::new(), &cfg, CancellationToken::new())
        .expect_err("string in i64 column rejected");
    assert_eq!(e.kind, ErrorKind::InvalidRequest);
    assert_eq!(e.code, "cell_type_mismatch");

    let req2: pctl::spec::QueryRequest = serde_json::from_value(json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [1, 2, 3] },
            { "name": "v", "data_type": "i64", "values": [1, 2] }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": 0.5, "method": "continuous" }
        ]
    }))
    .unwrap();
    let e = execute_request(&req2, &RequestId::new(), &cfg, CancellationToken::new())
        .expect_err("ragged columns rejected");
    assert_eq!(e.code, "ragged_columns");
}

#[test]
fn nan_measure_is_indeterminate_not_silently_ordered() {
    // JSON cannot carry NaN, so build the typed Arrow2 batch directly and run
    // the executor with a NaN measure cell.
    use pctl::batch::{TypedBatch, TypedColumn};
    use pctl::exec::{ExecOutcome, Executor};
    use pctl::validate::validate_request;

    let (cfg, _td) = test_config();
    let req: pctl::spec::QueryRequest = serde_json::from_value(json!({
        "group_by": "g",
        "columns": [
            { "name": "g", "data_type": "i64", "values": [0, 0, 0] },
            { "name": "v", "data_type": "f64", "values": [1.0, 0.0, 3.0] }
        ],
        "operators": [
            { "op": "percentile", "column": "v", "p": 0.5, "method": "continuous" }
        ]
    }))
    .unwrap();
    let plan = validate_request(&req).unwrap();
    // Replace the measure column with one containing NaN in the middle.
    let gcol: TypedColumn = vec![Some(0_i64), Some(0), Some(0)].into_iter().collect();
    let vcol: TypedColumn = vec![Some(1.0_f64), Some(f64::NAN), Some(3.0)]
        .into_iter()
        .collect();
    let batch = TypedBatch {
        columns: vec![gcol, vcol],
        len: 3,
    };

    let rid = RequestId::new();
    let outcome = Executor::start(&req, &plan, &batch, &rid, &cfg, CancellationToken::new())
        .unwrap()
        .run(0)
        .unwrap();
    let groups = match outcome {
        ExecOutcome::Complete(g, _) => g,
        other => panic!("{other:?}"),
    };
    let v = serde_json::to_value(&groups[0].results[0]).unwrap();
    assert_eq!(v["status"], json!("indeterminate"));
    assert_eq!(v["code"], json!("nan_measure"));
}
