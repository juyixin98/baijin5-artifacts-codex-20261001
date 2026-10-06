//! `rescheck` CLI: check one resolution proof file and print a JSON report.
//!
//! Usage:
//!   rescheck <proof-file> [--request-id ID] [--max-steps N]
//!            [--max-clause-lits N] [--max-total-lits N]
//!
//! Exit codes: 0 = verified, 1 = rejected, 2 = unverified, 3 = usage/IO error.

use rescheck::checker::{Checker, CheckerLimits, Verdict};
use std::fs::File;
use std::io::BufReader;
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    let mut file = None;
    let mut request_id = None;
    let mut limits = CheckerLimits::default();

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--request-id" => request_id = args.next(),
            "--max-steps" => match parse_opt(&mut args, "--max-steps") {
                Ok(v) => limits.max_steps = v,
                Err(code) => return code,
            },
            "--max-clause-lits" => match parse_opt(&mut args, "--max-clause-lits") {
                Ok(v) => limits.max_clause_lits = v,
                Err(code) => return code,
            },
            "--max-total-lits" => match parse_opt(&mut args, "--max-total-lits") {
                Ok(v) => limits.max_total_lits = v,
                Err(code) => return code,
            },
            "-h" | "--help" => {
                eprintln!(
                    "usage: rescheck <proof-file> [--request-id ID] [--max-steps N] \
                     [--max-clause-lits N] [--max-total-lits N]"
                );
                return ExitCode::from(0);
            }
            _ if file.is_none() && !arg.starts_with('-') => file = Some(arg),
            _ => {
                eprintln!("rescheck: unexpected argument '{arg}'");
                return ExitCode::from(3);
            }
        }
    }

    let Some(file) = file else {
        eprintln!("rescheck: missing proof file (see --help)");
        return ExitCode::from(3);
    };
    let request_id = request_id.unwrap_or_else(|| format!("cli:{file}"));

    let input = match File::open(&file) {
        Ok(f) => BufReader::new(f),
        Err(e) => {
            eprintln!("rescheck: cannot open {file}: {e}");
            return ExitCode::from(3);
        }
    };

    let report = Checker::new(limits, request_id).check_stream(input);
    println!("{}", report.to_json());

    match report.verdict {
        Verdict::Verified => ExitCode::from(0),
        Verdict::Rejected => ExitCode::from(1),
        Verdict::Unverified => ExitCode::from(2),
    }
}

fn parse_opt(args: &mut impl Iterator<Item = String>, name: &str) -> Result<usize, ExitCode> {
    let Some(raw) = args.next() else {
        eprintln!("rescheck: {name} needs a value");
        return Err(ExitCode::from(3));
    };
    raw.parse::<usize>().map_err(|_| {
        eprintln!("rescheck: {name}: invalid number '{raw}'");
        ExitCode::from(3)
    })
}
