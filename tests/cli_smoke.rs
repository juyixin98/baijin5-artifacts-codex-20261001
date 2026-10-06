//! End-to-end tests through the real CLI process: exit codes must match the
//! failure category and stdout/stderr must carry the JSON contract.
use std::process::Command;
fn cli() -> Command {
    Command::new(env!("CARGO_BIN_EXE_qecli"))
}

fn fixture(name: &str) -> String {
    concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/")
        .to_string()
        + name
}

#[test]
fn pipeline_mode_reports_run_id_and_value() {
    let out = cli()
        .args([
            "--model", &fixture("model_two.json"),
            "--formula", &fixture("formulas.json"),
            "--name", "alt_forall_exists",
            "--mode", "pipeline",
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    let stdout = String::from_utf8(out.stdout).unwrap();
    let json: serde_json::Value = serde_json::from_str(&stdout).unwrap();
    assert!(json["run_id"].as_str().unwrap().starts_with("run-"));
    assert_eq!(json["status"], "eliminated");
    assert_eq!(json["value"], false);
    assert_eq!(json["check"]["ok"], true);
    assert!(json["trace"].as_array().unwrap().len() >= 2);
}

#[test]
fn eval_mode_on_empty_domain_model() {
    let out = cli()
        .args([
            "--model", &fixture("model_empty.json"),
            "--formula", &fixture("formulas.json"),
            "--name", "vacuous_forall",
            "--mode", "eval",
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    let json: serde_json::Value =
        serde_json::from_str(&String::from_utf8(out.stdout).unwrap()).unwrap();
    assert_eq!(json["value"], true);
}

#[test]
fn qe_mode_with_tight_budget_marks_unknown() {
    let out = cli()
        .args([
            "--model", &fixture("model_two.json"),
            "--formula", &fixture("formulas.json"),
            "--name", "collide_names",
            "--mode", "qe",
            "--budget", "0",
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    let json: serde_json::Value =
        serde_json::from_str(&String::from_utf8(out.stdout).unwrap()).unwrap();
    assert_eq!(json["status"], "partial");
    assert_eq!(json["unknown"], true);
    assert!(json["remaining_quantifiers"].as_u64().unwrap() > 0);
}

#[test]
fn forbidden_empty_domain_exits_with_state_conflict() {
    let out = cli()
        .args([
            "--model", &fixture("model_empty_forbidden.json"),
            "--formula", &fixture("formulas.json"),
            "--name", "vacuous_forall",
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(3));
    let stderr = String::from_utf8(out.stderr).unwrap();
    let json: serde_json::Value = serde_json::from_str(&stderr).unwrap();
    assert_eq!(json["error"]["kind"], "state_conflict");
}

#[test]
fn missing_files_exit_with_invalid_input() {
    let out = cli()
        .args([
            "--model", "no/such/model.json",
            "--formula", &fixture("formulas.json"),
        ])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8(out.stderr).unwrap();
    assert!(stderr.contains("invalid_input"));
}
