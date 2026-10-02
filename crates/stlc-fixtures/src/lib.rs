//! Loading of hand-authored, reusable JSON fixtures.
//!
//! Fixtures may state terms either as raw AST (`term`) or as concrete syntax
//! (`term_source`), and signatures either as structured bindings or as
//! `"name : Type"` text lines. Every `expected_*` value is written down in
//! the fixture from a manual calculation; tests compare system output against
//! that data rather than regenerating it.

use std::path::{Path, PathBuf};

use serde::Deserialize;
use stlc_core::CheckRequest;
use stlc_proof::Binding;
use stlc_syntax::parse::{parse_term, parse_type};
use stlc_syntax::{Term, Type};

pub fn fixture_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExpectVerdict {
    Ok,
    TypeError,
    BudgetExhausted,
    ParseError,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Expected {
    pub verdict: ExpectVerdict,
    /// Discriminator of the expected type-error category, e.g.
    /// "expected_function", "domain_mismatch", "unknown_free_var",
    /// "if_guard_not_bool", "branch_mismatch".
    #[serde(default)]
    pub type_error_kind: Option<String>,
    /// Concrete surface syntax of the expected normal form.
    #[serde(default)]
    pub normal_form: Option<String>,
    #[serde(default)]
    pub steps: Option<usize>,
    #[serde(default)]
    pub free_vars: Vec<String>,
    #[serde(default)]
    pub result_type: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
enum TermInput {
    Source { term_source: String },
    Ast { term: Term },
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
enum SigEntry {
    Text(String),
    Structured {
        name: String,
        #[serde(rename = "type")]
        ty: Type,
    },
}

#[derive(Debug, Clone, Deserialize)]
struct RawCase {
    id: String,
    description: String,
    manual_reasoning: String,
    #[serde(default)]
    budget: Option<usize>,
    #[serde(default)]
    free_signature_source: Vec<String>,
    #[serde(default)]
    free_signature: Vec<SigEntry>,
    #[serde(flatten)]
    term_input: TermInput,
    expected: Expected,
}

/// One reusable test case. `term_source` is kept raw so malformed-input
/// fixtures can be loaded and surfaced as parse failures at run time.
#[derive(Debug, Clone)]
pub struct Case {
    pub id: String,
    pub description: String,
    pub manual_reasoning: String,
    pub term_source: String,
    pub term: Term,
    pub free_signature: Vec<Binding>,
    pub budget: usize,
    pub expected: Expected,
}

impl Case {
    pub fn request(&self) -> CheckRequest {
        CheckRequest {
            term: self.term.clone(),
            free_signature: self.free_signature.clone(),
            budget: self.budget,
        }
    }
}

fn parse_signature_entry(entry: &SigEntry) -> Binding {
    match entry {
        SigEntry::Structured { name, ty } => Binding {
            name: name.clone(),
            ty: ty.clone(),
        },
        SigEntry::Text(text) => {
            let (name, ty_src) = text
                .split_once(':')
                .unwrap_or_else(|| panic!("signature entry `{text}` must be `name : Type`"));
            Binding {
                name: name.trim().to_string(),
                ty: parse_type(ty_src.trim())
                    .unwrap_or_else(|e| panic!("bad type `{ty_src}`: {e}")),
            }
        }
    }
}

impl Case {
    pub fn load(path: &Path) -> Result<Self, LoadError> {
        let bytes = std::fs::read(path).map_err(|e| LoadError::Io {
            path: path.display().to_string(),
            message: e.to_string(),
        })?;
        let raw: RawCase = serde_json::from_slice(&bytes).map_err(|e| LoadError::Json {
            path: path.display().to_string(),
            message: e.to_string(),
        })?;
        // Parse best-effort; malformed fixtures store a placeholder so the
        // load succeeds and the failure is exercised through the core.
        let (term_source, term) = match raw.term_input {
            TermInput::Source { term_source } => {
                let parsed = parse_term(&term_source).unwrap_or_else(|_| Term::BoolLit {
                    value: false,
                });
                (term_source, parsed)
            }
            TermInput::Ast { term } => {
                let source = stlc_syntax::pretty::term_to_string(&term);
                (source, term)
            }
        };
        let mut signature: Vec<Binding> = raw
            .free_signature_source
            .iter()
            .map(|line| parse_signature_entry(&SigEntry::Text(line.clone())))
            .collect();
        signature.extend(raw.free_signature.iter().map(parse_signature_entry));
        Ok(Case {
            id: raw.id,
            description: raw.description,
            manual_reasoning: raw.manual_reasoning,
            term_source,
            term,
            free_signature: signature,
            budget: raw.budget.unwrap_or(1024),
            expected: raw.expected,
        })
    }

    pub fn load_all() -> Result<Vec<Case>, LoadError> {
        let mut cases = Vec::new();
        let dir = fixture_dir();
        let mut stack = vec![dir.clone()];
        while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(&dir).map_err(|e| LoadError::Io {
            path: dir.display().to_string(),
            message: e.to_string(),
        })? {
            let entry = entry.map_err(|e| LoadError::Io {
                path: dir.display().to_string(),
                message: e.to_string(),
            })?;
            let path = entry.path();
            let file_type = entry.file_type().map_err(|e| LoadError::Io {
                path: path.display().to_string(),
                message: e.to_string(),
            })?;
            if file_type.is_dir() {
                stack.push(path);
            } else if path.extension().and_then(|s| s.to_str()) == Some("json") {
                cases.push(Case::load(&path)?);
            }
        }
        }
        cases.sort_by(|a, b| a.id.cmp(&b.id));
        Ok(cases)
    }
}

#[derive(Debug, Clone)]
pub enum LoadError {
    Io { path: String, message: String },
    Json { path: String, message: String },
}

impl std::fmt::Display for LoadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LoadError::Io { path, message } => write!(f, "cannot read {path}: {message}"),
            LoadError::Json { path, message } => write!(f, "invalid fixture {path}: {message}"),
        }
    }
}

impl std::error::Error for LoadError {}
