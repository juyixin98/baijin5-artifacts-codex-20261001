//! Recursive-descent parser for propositional formulas.

use crate::formula::Formula;

#[derive(Debug, Clone, PartialEq, Eq)]
enum Token {
    Ident(String),
    KwTrue,
    KwFalse,
    Not,
    And,
    Or,
    Imp,
    LParen,
    RParen,
}

/// Parse error carrying a zero-based byte offset and human-readable reason.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    pub offset: usize,
    pub message: String,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "parse error at byte {}: {}", self.offset, self.message)
    }
}

impl std::error::Error for ParseError {}

fn tokenize(input: &str) -> Result<Vec<(Token, usize)>, ParseError> {
    let bytes = input.as_bytes();
    let mut tokens = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        let byte = bytes[i];
        match byte {
            b' ' | b'\t' | b'\n' | b'\r' => i += 1,
            b'!' => {
                tokens.push((Token::Not, i));
                i += 1;
            }
            b'&' => {
                tokens.push((Token::And, i));
                i += 1;
            }
            b'|' => {
                tokens.push((Token::Or, i));
                i += 1;
            }
            b'(' => {
                tokens.push((Token::LParen, i));
                i += 1;
            }
            b')' => {
                tokens.push((Token::RParen, i));
                i += 1;
            }
            b'-' => {
                if i + 1 < bytes.len() && bytes[i + 1] == b'>' {
                    tokens.push((Token::Imp, i));
                    i += 2;
                } else {
                    return Err(ParseError {
                        offset: i,
                        message: "expected '->' implication arrow".to_string(),
                    });
                }
            }
            c if c.is_ascii_alphabetic() || c == b'_' => {
                let start = i;
                i += 1;
                while i < bytes.len()
                    && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_')
                {
                    i += 1;
                }
                let word = &input[start..i];
                let token = match word {
                    "true" => Token::KwTrue,
                    "false" => Token::KwFalse,
                    _ => Token::Ident(word.to_string()),
                };
                tokens.push((token, start));
            }
            other => {
                return Err(ParseError {
                    offset: i,
                    message: format!("unexpected character {:?}", other as char),
                });
            }
        }
    }
    Ok(tokens)
}

struct Parser {
    tokens: Vec<(Token, usize)>,
    pos: usize,
}

impl Parser {
    fn parse_formula(&mut self) -> Result<Formula, ParseError> {
        self.parse_imp()
    }

    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.pos).map(|(t, _)| t)
    }

    fn pos_of_next(&self) -> usize {
        self.tokens
            .get(self.pos)
            .map(|(_, p)| *p)
            .unwrap_or(usize::MAX)
    }

    fn parse_imp(&mut self) -> Result<Formula, ParseError> {
        let left = self.parse_or()?;
        if self.peek() == Some(&Token::Imp) {
            self.pos += 1;
            let right = self.parse_imp()?;
            Ok(Formula::imp(left, right))
        } else {
            Ok(left)
        }
    }

    fn parse_or(&mut self) -> Result<Formula, ParseError> {
        let mut items = vec![self.parse_and()?];
        while self.peek() == Some(&Token::Or) {
            self.pos += 1;
            items.push(self.parse_and()?);
        }
        if items.len() == 1 {
            Ok(items.pop().unwrap())
        } else {
            Ok(Formula::Or(items))
        }
    }

    fn parse_and(&mut self) -> Result<Formula, ParseError> {
        let mut items = vec![self.parse_unary()?];
        while self.peek() == Some(&Token::And) {
            self.pos += 1;
            items.push(self.parse_unary()?);
        }
        if items.len() == 1 {
            Ok(items.pop().unwrap())
        } else {
            Ok(Formula::And(items))
        }
    }

    fn parse_unary(&mut self) -> Result<Formula, ParseError> {
        if self.peek() == Some(&Token::Not) {
            self.pos += 1;
            let inner = self.parse_unary()?;
            Ok(Formula::not(inner))
        } else {
            self.parse_primary()
        }
    }

    fn parse_primary(&mut self) -> Result<Formula, ParseError> {
        let (token, offset) = match self.tokens.get(self.pos) {
            Some(pair) => pair.clone(),
            None => {
                return Err(ParseError {
                    offset: self.pos_of_next(),
                    message: "unexpected end of input, expected a formula".to_string(),
                });
            }
        };
        self.pos += 1;
        match token {
            Token::KwTrue => Ok(Formula::Const(true)),
            Token::KwFalse => Ok(Formula::Const(false)),
            Token::Ident(name) => Ok(Formula::Var(name)),
            Token::LParen => {
                let inner = self.parse_formula()?;
                match self.peek() {
                    Some(Token::RParen) => {
                        self.pos += 1;
                        Ok(inner)
                    }
                    _ => Err(ParseError {
                        offset: self.pos_of_next(),
                        message: "expected closing ')'".to_string(),
                    }),
                }
            }
            other => Err(ParseError {
                offset,
                message: format!("expected atom or '(' but found {:?}", other),
            }),
        }
    }
}

/// Parse a propositional formula such as `(p -> q) & !r`.
pub fn parse(input: &str) -> Result<Formula, ParseError> {
    let tokens = tokenize(input)?;
    if tokens.is_empty() {
        return Err(ParseError {
            offset: 0,
            message: "empty input".to_string(),
        });
    }
    let mut parser = Parser { tokens, pos: 0 };
    let formula = parser.parse_formula()?;
    if parser.pos != parser.tokens.len() {
        let (_, offset) = parser.tokens[parser.pos].clone();
        return Err(ParseError {
            offset,
            message: "trailing tokens after complete formula".to_string(),
        });
    }
    Ok(formula)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_precedence_chain() {
        let f = parse("p & !q | r -> s").unwrap();
        assert_eq!(
            f,
            Formula::imp(
                Formula::Or(vec![
                    Formula::and(vec![
                        Formula::var("p"),
                        Formula::not(Formula::var("q"))
                    ]),
                    Formula::var("r")
                ]),
                Formula::var("s")
            )
        );
    }

    #[test]
    fn rejects_garbage() {
        assert!(parse("p &").is_err());
        assert!(parse("(p").is_err());
        assert!(parse("").is_err());
        assert!(parse("p q").is_err());
    }

    #[test]
    fn pretty_roundtrip_evaluates() {
        let f = parse("(a -> b) & (b | false) & true").unwrap();
        assert!(f.to_pretty().contains("a -> b"));
    }
}
