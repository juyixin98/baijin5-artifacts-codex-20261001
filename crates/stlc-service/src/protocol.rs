//! JSON wire protocol and friendly input forms.
//!
//! Terms may arrive as structured AST (`term`) or concrete syntax
//! (`term_source`). Types within signatures may likewise be AST or strings.

use serde::{Deserialize, Serialize};
use stlc_core::CheckRequest;
use stlc_proof::Binding;
use stlc_syntax::parse::{parse_term, parse_type};
use stlc_syntax::{Term, Type};

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum TermInput {
    Source { term_source: String },
    Ast { term: Term },
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum TypeInput {
    Source(String),
    Ast(Type),
}

impl TypeInput {
    pub fn parse(self) -> Result<Type, String> {
        match self {
            TypeInput::Source(src) => parse_type(&src).map_err(|e| e.to_string()),
            TypeInput::Ast(ty) => Ok(ty),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum SigEntry {
    Text(String),
    Structured {
        name: String,
        #[serde(rename = "type")]
        ty: TypeInput,
    },
}

#[derive(Debug, Clone, Deserialize)]
pub struct ApiRequest {
    #[serde(flatten)]
    pub term: TermInput,
    #[serde(default)]
    pub free_signature: Vec<SigEntry>,
    #[serde(default)]
    pub free_signature_source: Vec<String>,
    pub budget: Option<usize>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ApiError {
    pub ok: bool,
    pub category: &'static str,
    pub message: String,
}

impl ApiRequest {
    pub fn into_core(self, default_budget: usize) -> Result<CheckRequest, String> {
        let term = match self.term {
            TermInput::Source { term_source } => {
                parse_term(&term_source).map_err(|e| e.to_string())?
            }
            TermInput::Ast { term } => term,
        };
        let mut signature = Vec::new();
        for line in self.free_signature_source {
            let (name, ty_src) = line
                .split_once(':')
                .ok_or_else(|| format!("signature entry `{line}` must be `name : Type`"))?;
            signature.push(Binding {
                name: name.trim().to_string(),
                ty: parse_type(ty_src.trim()).map_err(|e| e.to_string())?,
            });
        }
        for entry in self.free_signature {
            match entry {
                SigEntry::Text(text) => {
                    let (name, ty_src) = text
                        .split_once(':')
                        .ok_or_else(|| format!("signature entry `{text}` must be `name : Type`"))?;
                    signature.push(Binding {
                        name: name.trim().to_string(),
                        ty: parse_type(ty_src.trim()).map_err(|e| e.to_string())?,
                    });
                }
                SigEntry::Structured { name, ty } => signature.push(Binding {
                    name,
                    ty: ty.parse()?,
                }),
            }
        }
        Ok(CheckRequest {
            term,
            free_signature: signature,
            budget: self.budget.unwrap_or(default_budget),
        })
    }
}

/// Standalone alpha-equivalence request.
#[derive(Debug, Clone, Deserialize)]
pub struct AlphaRequest {
    #[serde(flatten)]
    pub left: TermInput,
    #[serde(default)]
    pub right_source: Option<String>,
    #[serde(default)]
    pub right: Option<Term>,
}

impl AlphaRequest {
    pub fn terms(self) -> Result<(Term, Term), String> {
        let left = match self.left {
            TermInput::Source { term_source } => {
                parse_term(&term_source).map_err(|e| e.to_string())?
            }
            TermInput::Ast { term } => term,
        };
        let right = match (self.right_source, self.right) {
            (Some(src), _) => parse_term(&src).map_err(|e| e.to_string())?,
            (None, Some(term)) => term,
            (None, None) => return Err("missing `right` or `right_source`".to_string()),
        };
        Ok((left, right))
    }
}
