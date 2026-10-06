//! Command-line front end ("service call example") for the qe-fol pipeline.
//!
//! Usage:
//!   qecli --model PATH --formula PATH [--name NAME] [--mode MODE] [--budget N]
//!
//! * `--model PATH`    model JSON file (see fixtures/model_two.json)
//! * `--formula PATH`  formula JSON file; with `--name` it is a bundle file
//!                     (see fixtures/formulas.json) and NAME selects an entry
//! * `--mode MODE`     `eval` | `qe` | `pipeline` (default: pipeline)
//! * `--budget N`      max quantifier expansions (default: unlimited)
//!
//! Output: a JSON report on stdout. Failures print a JSON error object on
//! stderr and exit with a category-specific code: 2 invalid_input,
//! 3 state_conflict, 4 resource_exhausted, 5 computation_failed (also used
//! when the independent check of a pipeline run reports violations).
use qe_fol::error::{ErrorKind, QeError};
use qe_fol::eval::eval_closed;
use qe_fol::model::Model;
use qe_fol::proof::new_run_id;
use qe_fol::qe::Eliminator;
use qe_fol::run_pipeline;
use qe_fol::syntax::{Formula, FormulaBundle};
use std::process::ExitCode;
struct Args {
    model: String,
    formula: String,
    name: Option<String>,
    mode: String,
    budget: Option<u64>,
}

fn parse_args() -> Result<Args, QeError> {
    let mut model = None;
    let mut formula = None;
    let mut name = None;
    let mut mode = "pipeline".to_string();
    let mut budget = None;
    let mut it = std::env::args().skip(1);
    while let Some(arg) = it.next() {
        let mut take = |flag: &str| -> Result<String, QeError> {
            it.next()
                .ok_or_else(|| QeError::invalid_input(format!("missing value for {flag}")))
        };
        match arg.as_str() {
            "--model" => model = Some(take("--model")?),
            "--formula" => formula = Some(take("--formula")?),
            "--name" => name = Some(take("--name")?),
            "--mode" => mode = take("--mode")?,
            "--budget" => {
                let raw = take("--budget")?;
                budget = Some(raw.parse::<u64>().map_err(|_| {
                    QeError::invalid_input(format!(
                        "--budget expects a non-negative integer, got '{raw}'"
                    ))
                })?);
            }
            "--help" | "-h" => {
                println!("{}", usage());
                std::process::exit(0);
            }
            other => {
                return Err(QeError::invalid_input(format!(
                    "unknown argument '{other}'\n{}",
                    usage()
                )))
            }
        }
    }
    let model = model.ok_or_else(|| QeError::invalid_input("missing --model".to_string()))?;
    let formula =
        formula.ok_or_else(|| QeError::invalid_input("missing --formula".to_string()))?;
    match mode.as_str() {
        "eval" | "qe" | "pipeline" => {}
        other => {
            return Err(QeError::invalid_input(format!(
                "unknown mode '{other}', expected eval|qe|pipeline"
            )))
        }
    }
    Ok(Args {
        model,
        formula,
        name,
        mode,
        budget,
    })
}

fn usage() -> &'static str {
    "usage: qecli --model PATH --formula PATH [--name NAME] [--mode eval|qe|pipeline] [--budget N]"
}

fn read_file(path: &str) -> Result<String, QeError> {
    std::fs::read_to_string(path)
        .map_err(|e| QeError::invalid_input(format!("cannot read '{path}': {e}")))
}

fn load_formula(args: &Args) -> Result<Formula, QeError> {
    let text = read_file(&args.formula)?;
    match &args.name {
        Some(name) => {
            let bundle = FormulaBundle::from_json_str(&text)?;
            bundle
                .formulas
                .into_iter()
                .find(|f| &f.name == name)
                .map(|f| f.formula)
                .ok_or_else(|| {
                    QeError::invalid_input(format!("no formula named '{name}' in bundle"))
                })
        }
        None => Formula::from_json_str(&text),
    }
}

fn print_json<T: serde::Serialize>(value: &T) {
    println!(
        "{}",
        serde_json::to_string_pretty(value).expect("report serialization is infallible")
    );
}

fn run() -> Result<i32, QeError> {
    let args = parse_args()?;
    let model = Model::from_json_str(&read_file(&args.model)?)?;
    let formula = load_formula(&args)?;
    match args.mode.as_str() {
        "eval" => {
            let run_id = new_run_id();
            let value = eval_closed(&model, &formula)?;
            print_json(&serde_json::json!({
                "run_id": run_id,
                "mode": "eval",
                "model": model.name,
                "value": value,
            }));
            Ok(0)
        }
        "qe" => {
            let outcome = Eliminator::new(&model, args.budget).run(&formula)?;
            print_json(&outcome);
            Ok(0)
        }
        _ => {
            let report = run_pipeline(&model, &formula, args.budget)?;
            let ok = report.check.ok;
            print_json(&report);
            if ok {
                Ok(0)
            } else {
                Ok(ErrorKind::ComputationFailed.exit_code())
            }
        }
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(code) => ExitCode::from(code as u8),
        Err(e) => {
            let obj = serde_json::json!({
                "error": { "kind": e.kind.as_str(), "message": e.message }
            });
            eprintln!(
                "{}",
                serde_json::to_string_pretty(&obj).expect("error serialization is infallible")
            );
            ExitCode::from(e.exit_code() as u8)
        }
    }
}
