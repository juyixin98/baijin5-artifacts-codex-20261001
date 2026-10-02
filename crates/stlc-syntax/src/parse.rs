//! Concrete-syntax parser for STLC terms and types.
//!
//! Terms:
//! ```text
//! term ::= lam ID ':' type '.' term | 'if' term 'then' term 'else' term
//!        | app
//! app  ::= atom+
//! atom ::= ID | 'true' | 'false' | '(' term ')'
//! type ::= atomtype ('->' type)?
//! atomtype ::= 'Bool' | ID | '(' type ')'
//! ```
//! Application is left-associated; arrows are right-associated.

use crate::{Term, Type};

/// Parse error with a byte offset into the input.
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

#[derive(Debug, Clone, PartialEq, Eq)]
enum Tok {
 Ident(String, usize),
    Bool(bool, usize),
    Lam(usize),
    Colon(usize),
    Dot(usize),
    Arrow(usize),
    LParen(usize),
    RParen(usize),
    If(usize),
    Then(usize),
    Else(usize),
    Eof(usize),
}

impl Tok {
    fn pos(&self) -> usize {
        match self {
            Tok::Ident(_, p)
            | Tok::Bool(_, p)
            | Tok::Lam(p)
            | Tok::Colon(p)
            | Tok::Dot(p)
            | Tok::Arrow(p)
            | Tok::LParen(p)
            | Tok::RParen(p)
            | Tok::If(p)
            | Tok::Then(p)
            | Tok::Else(p)
            | Tok::Eof(p) => *p,
        }
    }
}

fn lex(src: &str) -> Result<Vec<Tok>, ParseError> {
    let bytes = src.as_bytes();
    let mut i = 0usize;
    let mut out = Vec::new();
    while i < bytes.len() {
        let b = bytes[i];
        match b {
            b' ' | b'\t' | b'\n' | b'\r' => i += 1,
            b'(' => {
                out.push(Tok::LParen(i));
                i += 1;
            }
            b')' => {
                out.push(Tok::RParen(i));
                i += 1;
            }
            b':' => {
                out.push(Tok::Colon(i));
                i += 1;
            }
            b'.' => {
                out.push(Tok::Dot(i));
                i += 1;
            }
            b'\\' => {
                out.push(Tok::Lam(i));
                i += 1;
            }
            b'\xce' if i + 1 < bytes.len() && bytes[i + 1] == 0xbb => {
                // UTF-8 for λ
                out.push(Tok::Lam(i));
                i += 2;
            }
            b'-' if i + 1 < bytes.len() && bytes[i + 1] == b'>' => {
                out.push(Tok::Arrow(i));
                i += 2;
            }
            c if c.is_ascii_alphabetic() || c == b'_' => {
                let start = i;
                i += 1;
                while i < bytes.len()
                    && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_')
                {
                    i += 1;
                }
                let word = &src[start..i];
                match word {
                    "lam" => out.push(Tok::Lam(start)),
                    "if" => out.push(Tok::If(start)),
                    "then" => out.push(Tok::Then(start)),
                    "else" => out.push(Tok::Else(start)),
                    "true" => out.push(Tok::Bool(true, start)),
                    "false" => out.push(Tok::Bool(false, start)),
                    _ => out.push(Tok::Ident(word.to_string(), start)),
                }
            }
            _ => {
                return Err(ParseError {
                    offset: i,
                    message: format!("unexpected character {:?}", b as char),
                })
            }
        }
    }
    out.push(Tok::Eof(i));
    Ok(out)
}

struct Parser {
    toks: Vec<Tok>,
    pos: usize,
}

impl Parser {
    fn err<T>(&self, message: impl Into<String>) -> Result<T, ParseError> {
        Err(ParseError {
            offset: self.toks[self.pos].pos(),
            message: message.into(),
        })
    }

    fn peek(&self) -> &Tok {
        &self.toks[self.pos]
    }

    fn bump(&mut self) -> Tok {
        let t = self.toks[self.pos].clone();
        if self.pos + 1 < self.toks.len() {
            self.pos += 1;
        }
        t
    }

    fn parse_term(&mut self) -> Result<Term, ParseError> {
        match self.peek().clone() {
            Tok::Lam(p) => {
                self.bump();
                let param = match self.bump() {
                    Tok::Ident(n, _) => n,
                    other => {
                        return Err(ParseError {
                            offset: other.pos(),
                            message: "expected parameter name after 'lam'".to_string(),
                        })
                    }
                };
                if !matches!(self.bump(), Tok::Colon(_)) {
                    return Err(ParseError {
                        offset: p,
                        message: "expected ':' after binder name".to_string(),
                    });
                }
                let param_ty = self.parse_type()?;
                if !matches!(self.bump(), Tok::Dot(_)) {
                    return Err(ParseError {
                        offset: p,
                        message: "expected '.' after binder type".to_string(),
                    });
                }
                let body = self.parse_term()?;
                Ok(Term::Abs {
                    param,
                    param_ty,
                    body: Box::new(body),
                })
            }
            Tok::If(_) => {
                self.bump();
                let cond = self.parse_term()?;
                if !matches!(self.bump(), Tok::Then(_)) {
                    return self.err("expected 'then'");
                }
                let then = self.parse_term()?;
                if !matches!(self.bump(), Tok::Else(_)) {
                    return self.err("expected 'else'");
                }
                let otherwise = self.parse_term()?;
                Ok(Term::If {
                    cond: Box::new(cond),
                    then: Box::new(then),
                    otherwise: Box::new(otherwise),
                })
            }
            _ => self.parse_app(),
        }
    }

    fn parse_app(&mut self) -> Result<Term, ParseError> {
        let mut head = self.parse_atom()?;
        loop {
            match self.peek() {
                Tok::Ident(_, _)
                | Tok::Bool(_, _)
                | Tok::Lam(_)
                | Tok::LParen(_)
                | Tok::If(_) => {
                    let arg = self.parse_atom()?;
                    head = Term::App {
                        func: Box::new(head),
                        arg: Box::new(arg),
                    };
                }
                _ => break,
            }
        }
        Ok(head)
    }

    fn parse_atom(&mut self) -> Result<Term, ParseError> {
        match self.bump() {
            Tok::Ident(name, _) => Ok(Term::Var { name }),
            Tok::Bool(value, _) => Ok(Term::BoolLit { value }),
            Tok::LParen(_) => {
                let inner = self.parse_term()?;
                if !matches!(self.bump(), Tok::RParen(_)) {
                    return self.err("expected ')'");
                }
                Ok(inner)
            }
            other => Err(ParseError {
                offset: other.pos(),
                message: "expected a term".to_string(),
            }),
        }
    }

    fn parse_type(&mut self) -> Result<Type, ParseError> {
        let domain = self.parse_atom_type()?;
        if matches!(self.peek(), Tok::Arrow(_)) {
            self.bump();
            let codomain = self.parse_type()?;
            Ok(Type::Arrow {
                domain: Box::new(domain),
                codomain: Box::new(codomain),
            })
        } else {
            Ok(domain)
        }
    }

    fn parse_atom_type(&mut self) -> Result<Type, ParseError> {
        match self.bump() {
            Tok::Ident(name, _) if name == "Bool" => Ok(Type::Bool),
            Tok::Ident(name, _) => Ok(Type::Base { name }),
            Tok::LParen(_) => {
                let inner = self.parse_type()?;
                if !matches!(self.bump(), Tok::RParen(_)) {
                    return self.err("expected ')' in type");
                }
                Ok(inner)
            }
            other => Err(ParseError {
                offset: other.pos(),
                message: "expected a type".to_string(),
            }),
        }
    }
}

/// Parse a term.
pub fn parse_term(src: &str) -> Result<Term, ParseError> {
    let mut parser = Parser {
        toks: lex(src)?,
        pos: 0,
    };
    let term = parser.parse_term()?;
    if !matches!(parser.peek(), Tok::Eof(_)) {
        return parser.err("trailing input after term");
    }
    Ok(term)
}

/// Parse a type.
pub fn parse_type(src: &str) -> Result<Type, ParseError> {
    let mut parser = Parser {
        toks: lex(src)?,
        pos: 0,
    };
    let ty = parser.parse_type()?;
    if !matches!(parser.peek(), Tok::Eof(_)) {
        return parser.err("trailing input after type");
    }
    Ok(ty)
}
