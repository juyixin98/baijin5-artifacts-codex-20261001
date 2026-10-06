//! Finite enumerated-domain models.
//!
//! A [`Model`] fixes a finite domain of named elements and an interpretation
//! for each predicate as an explicit set of tuples. The empty-domain policy
//! is explicit: a model with an empty domain is only valid when
//! `allow_empty_domain` is set, otherwise validation fails with
//! [`crate::error::ErrorKind::StateConflict`].
//!
//! JSON representation:
//! ```json
//! {
//!   "name": "two",
//!   "allow_empty_domain": false,
//!   "domain": ["a", "b"],
//!   "predicates": [
//!     {"name": "P", "arity": 1, "tuples": [["a"]]},
//!     {"name": "E", "arity": 1, "tuples": []}
//!   ]
//! }
//! ```

use crate::error::QeError;
use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone)]
pub struct Predicate {
    pub arity: usize,
    pub tuples: BTreeSet<Vec<String>>,
}

#[derive(Debug, Clone)]
pub struct Model {
    pub name: String,
    pub allow_empty_domain: bool,
    pub domain: Vec<String>,
    pub predicates: BTreeMap<String, Predicate>,
}

#[derive(Deserialize)]
struct ModelFile {
    name: String,
    #[serde(default)]
    allow_empty_domain: bool,
    domain: Vec<String>,
    #[serde(default)]
    predicates: Vec<PredicateFile>,
}

#[derive(Deserialize)]
struct PredicateFile {
    name: String,
    arity: usize,
    #[serde(default)]
    tuples: Vec<Vec<String>>,
}

impl Model {
    pub fn from_json_str(s: &str) -> Result<Model, QeError> {
        let file: ModelFile = serde_json::from_str(s)
            .map_err(|e| QeError::invalid_input(format!("model JSON parse error: {e}")))?;
        let mut predicates = BTreeMap::new();
        for p in file.predicates {
            if predicates.contains_key(&p.name) {
                return Err(QeError::state_conflict(format!(
                    "duplicate predicate declaration '{}'",
                    p.name
                )));
            }
            predicates.insert(
                p.name,
                Predicate {
                    arity: p.arity,
                    tuples: p.tuples.into_iter().collect(),
                },
            );
        }
        let model = Model {
            name: file.name,
            allow_empty_domain: file.allow_empty_domain,
            domain: file.domain,
            predicates,
        };
        model.validate()?;
        Ok(model)
    }

    /// Structural and policy validation. Called on load and again before
    /// evaluation/elimination so programmatically built models are checked
    /// by the same contract.
    pub fn validate(&self) -> Result<(), QeError> {
        if self.domain.is_empty() && !self.allow_empty_domain {
            return Err(QeError::state_conflict(
                "domain is empty but model policy 'allow_empty_domain' is false".to_string(),
            ));
        }
        let mut seen = BTreeSet::new();
        for el in &self.domain {
            if !seen.insert(el) {
                return Err(QeError::state_conflict(format!(
                    "duplicate domain element '{el}'"
                )));
            }
        }
        for (name, p) in &self.predicates {
            if p.arity == 0 {
                return Err(QeError::invalid_input(format!(
                    "predicate '{name}' has arity 0; only arity >= 1 is supported"
                )));
            }
            for tuple in &p.tuples {
                if tuple.len() != p.arity {
                    return Err(QeError::invalid_input(format!(
                        "predicate '{name}' tuple {tuple:?} has arity {}, expected {}",
                        tuple.len(),
                        p.arity
                    )));
                }
                for el in tuple {
                    if !self.domain.contains(el) {
                        return Err(QeError::invalid_input(format!(
                            "predicate '{name}' tuple references unknown domain element '{el}'"
                        )));
                    }
                }
            }
        }
        Ok(())
    }

    pub fn is_domain_element(&self, el: &str) -> bool {
        self.domain.iter().any(|e| e == el)
    }
}
