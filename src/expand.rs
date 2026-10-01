//! Quantifier elimination core for finite enumerated domains.
//!
//! Expansion rewrites a quantifier over a finite sort into a conjunction
//! (`forall`) or disjunction (`exists`) of ground instances. Each produced
//! binding consumes one unit of `instantiation_budget`.
//!
//! Budget semantics (contract point 3):
//! * Instantiation order is deterministic: leftmost-outermost quantifier first,
//!   elements in domain declaration order.
//! * One unit is charged every time a bound variable is assigned an element.
//!   A nested quantifier is expanded per branch, so the cost of `∀x∃y` over
//!   domains of sizes m,n is m + m·n.
//! * If a quantifier node cannot be fully paid for, that node is left in the
//!   result unchanged, already charged instances inside it are rolled back
//!   (the whole node is restored) and the verdict becomes `unknown`.
//! * Connective nodes are transparent splicing points: already expanded
//!   siblings are kept, the aborting child is preserved verbatim and later
//!   siblings are left unvisited.
//! * A fully expanded formula larger than `node_cap` produces a distinct
//!   `ResourceExhausted/node_cap` error instead of a truth value.

use crate::error::{QeError, QeResult};
use crate::model::Model;
use crate::proof::{RunLogger, Step};
use crate::subst::rename_free;
use crate::syntax::{Formula, Term};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExpansionReport {
    pub formula: Formula,
    pub used: u64,
    pub limit: u64,
    /// True when budget ran out and at least one quantifier node was retained.
    pub exhausted: bool,
}

/// Marker used to abort out of the currently restoring quantifier node.
struct Aborted;

pub fn expand_quantifiers(
    model: &Model,
    formula: &Formula,
    budget: u64,
    logger: &mut dyn RunLogger,
) -> QeResult<ExpansionReport> {
    let mut cx = Cx {
        model,
        used: 0,
        limit: budget,
        exhausted: false,
    };
    let expanded = match cx.go(formula, logger) {
        Ok(part) => part,
        // The root is itself a quantifier on the abort path; preserve it.
        Err(Aborted) => formula.clone(),
    };
    Ok(ExpansionReport {
        formula: expanded,
        used: cx.used,
        limit: budget,
        exhausted: cx.exhausted,
    })
}

struct Cx<'a> {
    model: &'a Model,
    used: u64,
    limit: u64,
    exhausted: bool,
}

impl<'a> Cx<'a> {
    fn go(&mut self, f: &Formula, logger: &mut dyn RunLogger) -> Result<Formula, Aborted> {
        if self.exhausted {
            return Err(Aborted);
        }
        match f {
            Formula::Bool { .. } | Formula::Pred { .. } | Formula::Eq { .. } => Ok(f.clone()),
            Formula::Not { inner } => self
                .go(inner, logger)
                .map(|p| Formula::Not { inner: Box::new(p) }),
            Formula::And { children } => self.walk_join(children, true, logger),
            Formula::Or { children } => self.walk_join(children, false, logger),
            Formula::Impl { left, right } => {
                let l = self.go(left, logger)?;
                match self.go(right, logger) {
                    Ok(r) => Ok(Formula::Impl {
                        left: Box::new(l),
                        right: Box::new(r),
                    }),
                    Err(Aborted) => Ok(Formula::Impl {
                        left: Box::new(l),
                        right: right.clone(),
                    }),
                }
            }
            Formula::Iff { left, right } => {
                let l = self.go(left, logger)?;
                match self.go(right, logger) {
                    Ok(r) => Ok(Formula::Iff {
                        left: Box::new(l),
                        right: Box::new(r),
                    }),
                    Err(Aborted) => Ok(Formula::Iff {
                        left: Box::new(l),
                        right: right.clone(),
                    }),
                }
            }
            Formula::Forall { var, sort, inner } => self.quantifier(var, sort, inner, true, logger),
            Formula::Exists { var, sort, inner } => {
                self.quantifier(var, sort, inner, false, logger)
            }
        }
    }

    /// Walk connective children, splicing completed expansions and stopping at
    /// the first abort while preserving the aborting child and later siblings.
    fn walk_join(
        &mut self,
        children: &[Formula],
        is_and: bool,
        logger: &mut dyn RunLogger,
    ) -> Result<Formula, Aborted> {
        let mut out = Vec::with_capacity(children.len());
        let mut aborted = false;
        for child in children {
            match self.go(child, logger) {
                Ok(part) => out.push(part),
                Err(Aborted) => {
                    out.push(child.clone());
                    aborted = true;
                    break;
                }
            }
        }
        // Remaining siblings (after the abort point) are never visited; keep
        // them verbatim so no unhandled quantifier silently disappears.
        if aborted {
            let start = out.len(); // aborting child already appended
            let original: Vec<&Formula> = children.iter().collect();
            let consumed = start; // expanded + aborting child accounted for
            for child in original.into_iter().skip(consumed) {
                out.push(child.clone());
            }
            let join = if is_and {
                Formula::And { children: out }
            } else {
                Formula::Or { children: out }
            };
            return Ok(join);
        }
        Ok(if is_and {
            flatten_and(out)
        } else {
            flatten_or(out)
        })
    }

    fn quantifier(
        &mut self,
        var: &str,
        sort: &str,
        inner: &Formula,
        is_forall: bool,
        logger: &mut dyn RunLogger,
    ) -> Result<Formula, Aborted> {
        let kind = if is_forall { "forall" } else { "exists" };
        let domain: Vec<String> = match self.model.sort(sort) {
            Some(s) => s.elements.clone(),
            None => {
                self.exhausted = true;
                return Err(Aborted);
            }
        };

        logger.emit(Step::ExpandStart {
            seq: 0,
            quantifier: kind.to_string(),
            var: var.to_string(),
            sort: sort.to_string(),
            size: domain.len(),
        });

        // Empty domains collapse without spending budget; whether they are
        // permitted at all is enforced by the service policy.
        if domain.is_empty() {
            logger.emit(Step::ExpandDone {
                seq: 0,
                quantifier: kind.to_string(),
                instances: 0,
                remaining_budget: self.limit - self.used,
            });
            return Ok(Formula::Bool { value: is_forall });
        }

        // Snapshot before this node does any work so a nested abort rolls back
        // every instance produced inside the whole restored node.
        let start_used = self.used;
        let size = domain.len() as u64;
        if self.used.saturating_add(size) > self.limit {
            self.exhausted = true;
            logger.emit(Step::BudgetExhausted {
                seq: 0,
                quantifier: kind.to_string(),
                var: var.to_string(),
                sort: sort.to_string(),
                used: self.used,
                limit: self.limit,
            });
            return Err(Aborted);
        }

        let mut instances: Vec<Formula> = Vec::with_capacity(domain.len());
        let walk: Result<(), Aborted> = (|| {
            for element in &domain {
                self.used += 1;
                logger.emit(Step::Instantiate {
                    seq: 0,
                    quantifier: kind.to_string(),
                    var: var.to_string(),
                    value: element.clone(),
                    used: self.used,
                    limit: self.limit,
                });
                let literal = Term::Elem {
                    value: element.clone(),
                    sort: Some(sort.to_string()),
                };
                let substituted = rename_free(inner, var, &literal);
                instances.push(self.go(&substituted, logger)?);
            }
            Ok(())
        })();

        if walk.is_err() {
            let rolled_back = self.used.saturating_sub(start_used);
            self.used = start_used;
            logger.emit(Step::RollbackInstances {
                seq: 0,
                quantifier: kind.to_string(),
                rolled_back,
                used_after: self.used,
            });
            return Err(Aborted);
        }

        logger.emit(Step::ExpandDone {
            seq: 0,
            quantifier: kind.to_string(),
            instances: size,
            remaining_budget: self.limit - self.used,
        });

        Ok(if is_forall {
            flatten_and(instances)
        } else {
            flatten_or(instances)
        })
    }
}

/// Service entry point; applies the node cap guard after successful
/// elimination (skipped when budget was exhausted and quantifiers remain).
pub fn eliminate(
    model: &Model,
    formula: &Formula,
    budget: u64,
    node_cap: u64,
    logger: &mut dyn RunLogger,
) -> QeResult<ExpansionReport> {
    let report = expand_quantifiers(model, formula, budget, logger)?;
    if !report.exhausted {
        let nodes = report.formula.node_count() as u64;
        if nodes > node_cap {
            return Err(QeError::resource(
                "node_cap",
                format!(
                    "expanded formula has {} nodes which exceeds the node cap {}",
                    nodes, node_cap
                ),
            ));
        }
    }
    Ok(report)
}

fn flatten_and(children: Vec<Formula>) -> Formula {
    let mut flat = Vec::with_capacity(children.len());
    for child in children {
        if let Formula::And { children: nested } = child {
            flat.extend(nested);
        } else {
            flat.push(child);
        }
    }
    match flat.len() {
        0 => Formula::Bool { value: true },
        1 => flat.remove(0),
        _ => Formula::And { children: flat },
    }
}

fn flatten_or(children: Vec<Formula>) -> Formula {
    let mut flat = Vec::with_capacity(children.len());
    for child in children {
        if let Formula::Or { children: nested } = child {
            flat.extend(nested);
        } else {
            flat.push(child);
        }
    }
    match flat.len() {
        0 => Formula::Bool { value: false },
        1 => flat.remove(0),
        _ => Formula::Or { children: flat },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::*;
    use crate::proof::NullLogger;
    use crate::syntax::build::*;

    fn model_two() -> Model {
        Model {
            sorts: vec![
                Sort {
                    name: "X".to_string(),
                    elements: vec!["x1".into(), "x2".into()],
                },
                Sort {
                    name: "Y".to_string(),
                    elements: vec!["y1".into(), "y2".into()],
                },
                Sort {
                    name: "E".to_string(),
                    elements: vec![],
                },
            ],
            constants: vec![],
            functions: vec![],
            predicates: vec![Predicate {
                name: "p".into(),
                param_sorts: vec!["X".into()],
                facts: vec![],
            }],
        }
    }

    #[test]
    fn full_expansion_is_quantifier_free() {
        let m = model_two();
        let f = forall("x", "X", pred("p", vec![var("x")]));
        let r = expand_quantifiers(&m, &f, 100, &mut NullLogger).unwrap();
        assert!(r.formula.is_quantifier_free());
        assert_eq!(r.used, 2);
        assert!(!r.exhausted);
    }

    #[test]
    fn insufficient_budget_retains_quantifier() {
        let m = model_two();
        let f = forall("x", "X", pred("p", vec![var("x")]));
        let r = expand_quantifiers(&m, &f, 1, &mut NullLogger).unwrap();
        assert!(r.exhausted);
        assert_eq!(
            r.formula, f,
            "whole node must be preserved on partial payment"
        );
        assert_eq!(r.used, 0);
    }

    #[test]
    fn nested_abort_rolls_back_parent_and_keeps_root() {
        let m = model_two();
        // ∀x∈X(2). ∃y∈Y(2). p(x); full bindings = 2 + 4 = 6.
        let f = forall("x", "X", exists("y", "Y", pred("p", vec![var("x")])));
        // budget 3: first branch completes (1 + 2 = 3), second branch fails.
        let r = expand_quantifiers(&m, &f, 3, &mut NullLogger).unwrap();
        assert!(r.exhausted);
        assert_eq!(r.formula, f, "root quantifier retained; used rolled back");
        assert_eq!(r.used, 0);
    }

    #[test]
    fn connective_splices_completed_sibling() {
        let m = model_two();
        let node = forall("x", "X", pred("p", vec![var("x")]));
        let f = and(vec![node.clone(), node.clone()]);
        let r = expand_quantifiers(&m, &f, 2, &mut NullLogger).unwrap();
        assert!(r.exhausted);
        match r.formula {
            Formula::And { children } => {
                assert_eq!(children.len(), 2);
                assert!(children[0].is_quantifier_free(), "first sibling expanded");
                assert_eq!(children[1], node, "second sibling retained");
            }
            other => panic!("expected and at root, got {:?}", other),
        }
    }

    #[test]
    fn empty_domain_collapses_without_budget_spend() {
        let m = model_two();
        let f_all = forall("z", "E", pred("p", vec![]));
        let r = expand_quantifiers(&m, &f_all, 0, &mut NullLogger).unwrap();
        assert_eq!(r.formula, Formula::Bool { value: true });
        assert_eq!(r.used, 0);

        let f_ex = exists("z", "E", pred("p", vec![]));
        let r = expand_quantifiers(&m, &f_ex, 0, &mut NullLogger).unwrap();
        assert_eq!(r.formula, Formula::Bool { value: false });
    }

    #[test]
    fn node_cap_guards_expanded_size() {
        let m = model_two();
        let f = forall("x", "X", pred("p", vec![var("x")]));
        let err = eliminate(&m, &f, 100, 1, &mut NullLogger).unwrap_err();
        assert_eq!(err.kind, crate::error::ErrorKind::ResourceExhausted);
        assert_eq!(err.code, "node_cap");
    }
}
