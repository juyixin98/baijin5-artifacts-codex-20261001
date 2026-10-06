//! Proof record format: a line-based, stream-friendly textual encoding.
//!
//! Grammar (tokens separated by whitespace, `#` starts a comment):
//!
//! ```text
//! c <id> <lit>... 0                  axiom (input clause)
//! r <id> <left> <right> <pivot> <lit>... 0   resolution step with claimed resolvent
//! d <id>                              delete a stored clause
//! ```
//!
//! Literals are DIMACS-style signed integers; `0` terminates a literal list.
//! The claimed resolvent of a resolution step is written explicitly so the
//! checker compares an independent claim against its own computation.

use crate::syntax::{Literal, SyntaxError};
use std::fmt;
use std::io::BufRead;

/// One parsed proof step.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum ProofStep {
    Axiom { id: u64, lits: Vec<Literal> },
    Resolve {
        id: u64,
        left: u64,
        right: u64,
        pivot: u32,
        claimed: Vec<Literal>,
    },
    Delete { id: u64 },
}

impl ProofStep {
    pub fn id(&self) -> u64 {
        match self {
            ProofStep::Axiom { id, .. }
            | ProofStep::Resolve { id, .. }
            | ProofStep::Delete { id } => *id,
        }
    }
}

/// A line that could not be parsed into a proof step.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ParseError {
    pub line: usize,
    pub message: String,
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "line {}: {}", self.line, self.message)
    }
}

fn err(line: usize, message: impl Into<String>) -> ParseError {
    ParseError {
        line,
        message: message.into(),
    }
}

fn next_u64(
    tokens: &mut std::str::SplitWhitespace,
    lineno: usize,
    what: &str,
) -> Result<u64, ParseError> {
    tokens
        .next()
        .ok_or_else(|| err(lineno, format!("missing {what}")))?
        .parse::<u64>()
        .map_err(|_| err(lineno, format!("invalid {what}")))
}

fn literal_list(
    tokens: &mut std::str::SplitWhitespace,
    lineno: usize,
) -> Result<Vec<Literal>, ParseError> {
    let mut lits = Vec::new();
    loop {
        let tok = tokens
            .next()
            .ok_or_else(|| err(lineno, "unterminated literal list (missing 0)"))?;
        let value: i64 = tok
            .parse()
            .map_err(|_| err(lineno, format!("invalid literal '{tok}'")))?;
        if value == 0 {
            break;
        }
        lits.push(
            Literal::from_dimacs(value).map_err(|e: SyntaxError| err(lineno, e.to_string()))?,
        );
    }
    if tokens.next().is_some() {
        return Err(err(lineno, "trailing tokens after clause terminator"));
    }
    Ok(lits)
}

/// Parse one line. Returns `Ok(None)` for blank lines and comments.
pub fn parse_line(raw: &str, lineno: usize) -> Result<Option<ProofStep>, ParseError> {
    let text = match raw.find('#') {
        Some(i) => &raw[..i],
        None => raw,
    };
    let mut tokens = text.split_whitespace();
    let Some(keyword) = tokens.next() else {
        return Ok(None);
    };

    let step = match keyword {
        "c" => {
            let id = next_u64(&mut tokens, lineno, "clause id")?;
            let lits = literal_list(&mut tokens, lineno)?;
            ProofStep::Axiom { id, lits }
        }
        "r" => {
            let id = next_u64(&mut tokens, lineno, "step id")?;
            let left = next_u64(&mut tokens, lineno, "left parent id")?;
            let right = next_u64(&mut tokens, lineno, "right parent id")?;
            let pivot = next_u64(&mut tokens, lineno, "pivot")?;
            let pivot = u32::try_from(pivot)
                .ok()
                .filter(|&p| p > 0)
                .ok_or_else(|| err(lineno, "pivot must be a positive variable id"))?;
            let claimed = literal_list(&mut tokens, lineno)?;
            ProofStep::Resolve {
                id,
                left,
                right,
                pivot,
                claimed,
            }
        }
        "d" => {
            let id = next_u64(&mut tokens, lineno, "clause id")?;
            if tokens.next().is_some() {
                return Err(err(lineno, "trailing tokens after delete"));
            }
            ProofStep::Delete { id }
        }
        other => return Err(err(lineno, format!("unknown record kind '{other}'"))),
    };
    Ok(Some(step))
}

/// Streaming iterator over proof steps read from any buffered source.
/// Yields `Err(ParseError)` on the first malformed line and stops.
pub struct StepReader<R> {
    reader: R,
    buf: String,
    lineno: usize,
    done: bool,
}

impl<R: BufRead> StepReader<R> {
    pub fn new(reader: R) -> Self {
        StepReader {
            reader,
            buf: String::new(),
            lineno: 0,
            done: false,
        }
    }
}

impl<R: BufRead> Iterator for StepReader<R> {
    type Item = Result<ProofStep, ParseError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        loop {
            self.buf.clear();
            match self.reader.read_line(&mut self.buf) {
                Ok(0) => {
                    self.done = true;
                    return None;
                }
                Ok(_) => {
                    self.lineno += 1;
                    match parse_line(&self.buf, self.lineno) {
                        Ok(Some(step)) => return Some(Ok(step)),
                        Ok(None) => continue,
                        Err(e) => {
                            self.done = true;
                            return Some(Err(e));
                        }
                    }
                }
                Err(io) => {
                    self.done = true;
                    return Some(Err(err(self.lineno + 1, format!("I/O error: {io}"))));
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_axiom() {
        let step = parse_line("c 7 1 -2 0", 1).unwrap().unwrap();
        assert_eq!(
            step,
            ProofStep::Axiom {
                id: 7,
                lits: vec![Literal::new(1, true), Literal::new(2, false)],
            }
        );
    }

    #[test]
    fn parses_resolution_with_empty_claim() {
        let step = parse_line("r 9 4 5 2 0", 3).unwrap().unwrap();
        assert_eq!(
            step,
            ProofStep::Resolve {
                id: 9,
                left: 4,
                right: 5,
                pivot: 2,
                claimed: vec![],
            }
        );
    }

    #[test]
    fn skips_comments_and_blanks() {
        assert_eq!(parse_line("# hello", 1).unwrap(), None);
        assert_eq!(parse_line("   ", 2).unwrap(), None);
        assert!(parse_line("c 1 1 0  # trailing comment", 3).unwrap().is_some());
    }

    #[test]
    fn rejects_trailing_tokens() {
        assert!(parse_line("c 1 1 0 2", 1).is_err());
    }

    #[test]
    fn rejects_unterminated_list() {
        assert!(parse_line("c 1 1 2", 1).is_err());
    }
}
