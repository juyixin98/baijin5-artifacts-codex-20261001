//! Minimal hand-rolled argument parsing (no extra runtime dependencies).

use trs_syntax::{Error, ErrorKind, Limits};

pub enum Command {
    Check {
        system: String,
        cert: String,
        proof_out: Option<String>,
        limits: Limits,
    },
    Verify {
        system: String,
        cert: String,
        proof: String,
        limits: Limits,
    },
    GenFixtures {
        out: String,
    },
    Help,
}

pub const USAGE: &str = "\
trs-cli — termination certificate checker for term rewriting systems
         (linear interpretations over the naturals)

USAGE:
  trs-cli check  --system <system.json> --cert <cert.json> [--proof-out <proof.json>] [LIMITS]
  trs-cli verify --system <system.json> --cert <cert.json> --proof <proof.json> [LIMITS]
  trs-cli gen-fixtures --out <dir>
  trs-cli help

LIMITS (all optional, defaults in parentheses):
  --max-symbols N (1024)   --max-rules N (4096)     --max-arity N (64)
  --max-term-depth N (512) --max-term-nodes N (100000)
  --max-coefficient N (1000000000)

EXIT CODES:
  0  check: certificate accepted (terminating) / verify: record agrees
  1  check: certificate rejected (NOT a non-termination claim)
  2  input error        3  state conflict (verify: record disagrees)
  4  resource exhausted 5  computation failure";

fn parse_number<T>(flag: &str, value: Option<String>) -> Result<T, Error>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    let raw = value.ok_or_else(|| Error::new(ErrorKind::Parse, format!("flag '{}' needs a value", flag)))?;
    raw.parse::<T>()
        .map_err(|e| Error::new(ErrorKind::Parse, format!("invalid value for '{}': {}", flag, e)))
}

struct FlagParser {
    args: std::vec::IntoIter<String>,
    limits: Limits,
}

impl FlagParser {
    fn next_value(&mut self) -> Option<String> {
        self.args.next()
    }

    fn apply_limit(&mut self, flag: &str) -> Result<bool, Error> {
        match flag {
            "--max-symbols" => self.limits.max_symbols = parse_number(flag, self.next_value())?,
            "--max-rules" => self.limits.max_rules = parse_number(flag, self.next_value())?,
            "--max-arity" => self.limits.max_arity = parse_number(flag, self.next_value())?,
            "--max-term-depth" => self.limits.max_term_depth = parse_number(flag, self.next_value())?,
            "--max-term-nodes" => self.limits.max_term_nodes = parse_number(flag, self.next_value())?,
            "--max-coefficient" => {
                self.limits.max_coefficient = parse_number(flag, self.next_value())?
            }
            _ => return Ok(false),
        }
        Ok(true)
    }
}

pub fn parse_args(argv: Vec<String>) -> Result<Command, Error> {
    let mut iter = argv.into_iter();
    let subcommand = iter.next().unwrap_or_else(|| "help".to_string());
    let mut flags = FlagParser {
        args: iter.collect::<Vec<_>>().into_iter(),
        limits: Limits::default(),
    };

    match subcommand.as_str() {
        "check" => {
            let mut system = None;
            let mut cert = None;
            let mut proof_out = None;
            while let Some(flag) = flags.args.next() {
                match flag.as_str() {
                    "--system" => system = Some(flags.next_value().ok_or_else(|| missing("--system"))?),
                    "--cert" => cert = Some(flags.next_value().ok_or_else(|| missing("--cert"))?),
                    "--proof-out" => {
                        proof_out = Some(flags.next_value().ok_or_else(|| missing("--proof-out"))?)
                    }
                    other => {
                        if !flags.apply_limit(other)? {
                            return Err(Error::new(
                                ErrorKind::Parse,
                                format!("unknown flag '{}' for subcommand 'check'", other),
                            ));
                        }
                    }
                }
            }
            Ok(Command::Check {
                system: system.ok_or_else(|| missing("--system"))?,
                cert: cert.ok_or_else(|| missing("--cert"))?,
                proof_out,
                limits: flags.limits,
            })
        }
        "verify" => {
            let mut system = None;
            let mut cert = None;
            let mut proof = None;
            while let Some(flag) = flags.args.next() {
                match flag.as_str() {
                    "--system" => system = Some(flags.next_value().ok_or_else(|| missing("--system"))?),
                    "--cert" => cert = Some(flags.next_value().ok_or_else(|| missing("--cert"))?),
                    "--proof" => proof = Some(flags.next_value().ok_or_else(|| missing("--proof"))?),
                    other => {
                        if !flags.apply_limit(other)? {
                            return Err(Error::new(
                                ErrorKind::Parse,
                                format!("unknown flag '{}' for subcommand 'verify'", other),
                            ));
                        }
                    }
                }
            }
            Ok(Command::Verify {
                system: system.ok_or_else(|| missing("--system"))?,
                cert: cert.ok_or_else(|| missing("--cert"))?,
                proof: proof.ok_or_else(|| missing("--proof"))?,
                limits: flags.limits,
            })
        }
        "gen-fixtures" => {
            let mut out = None;
            while let Some(flag) = flags.args.next() {
                match flag.as_str() {
                    "--out" => out = Some(flags.next_value().ok_or_else(|| missing("--out"))?),
                    other => {
                        return Err(Error::new(
                            ErrorKind::Parse,
                            format!("unknown flag '{}' for subcommand 'gen-fixtures'", other),
                        ))
                    }
                }
            }
            Ok(Command::GenFixtures {
                out: out.ok_or_else(|| missing("--out"))?,
            })
        }
        "help" | "--help" | "-h" => Ok(Command::Help),
        other => Err(Error::new(
            ErrorKind::Parse,
            format!("unknown subcommand '{}' (expected check|verify|gen-fixtures|help)", other),
        )),
    }
}

fn missing(flag: &str) -> Error {
    Error::new(ErrorKind::Parse, format!("missing required flag '{}'", flag))
}
