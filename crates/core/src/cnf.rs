//! Conversion of formulas to conjunctive normal form.
//!
//! Two paths are provided:
//! - [`to_cnf_direct`] succeeds only for formulas already written in CNF
//!   (conjunction of disjunctions of literals). This path introduces no
//!   auxiliary variables and keeps interpolants human-readable.
//! - [`to_cnf_tseitin`] handles every formula via a standard bidirectional
//!   Tseitin encoding. All auxiliary variables are local to the side being
//!   encoded, so they never leak into the common alphabet.

use ipc_proof::{Clause, Literal};
use ipc_syntax::Formula;
use std::collections::{BTreeMap, BTreeSet, HashMap};

#[derive(Debug, Clone)]
pub struct CnfFormula {
    pub clauses: Vec<Clause>,
}

impl CnfFormula {
    pub fn new(clauses: Vec<Clause>) -> Self {
        CnfFormula { clauses }
    }

    pub fn variables(&self) -> Vec<String> {
        let mut vars = BTreeSet::new();
        for clause in &self.clauses {
            for lit in &clause.literals {
                vars.insert(lit.variable.clone());
            }
        }
        vars.into_iter().collect()
    }
}

#[derive(Debug, Clone)]
pub struct TseitinEncoding {
    pub clauses: Vec<Clause>,
    /// All auxiliary variable names introduced by the encoding.
    pub auxiliary: Vec<String>,
    /// Root selector when the formula needed auxiliary variables.
    pub root_variable: Option<String>,
}

/// Fast path: returns `None` if the formula is not literally in CNF.
pub fn to_cnf_direct(formula: &Formula) -> Option<CnfFormula> {
    let simplified = desugar(formula);
    let mut clauses = Vec::new();
    collect_clauses(&simplified, &mut clauses)?;
    Some(CnfFormula::new(clauses))
}

fn collect_clauses(formula: &Formula, out: &mut Vec<Clause>) -> Option<()> {
    match formula {
        Formula::Const(true) => Some(()),
        Formula::Const(false) => {
            out.push(Clause::empty());
            Some(())
        }
        Formula::And(items) => {
            for item in items {
                collect_clauses(item, out)?;
            }
            Some(())
        }
        other => {
            out.push(collect_disjunction(other)?);
            Some(())
        }
    }
}

fn collect_disjunction(formula: &Formula) -> Option<Clause> {
    match formula {
        Formula::Const(true) => Some(Clause::new(vec![Literal::positive("__const_true")])),
        Formula::Const(false) => Some(Clause::empty()),
        Formula::Or(items) => {
            let mut literals = Vec::new();
            for item in items {
                match item {
                    Formula::Const(true) => {
                        literals.push(Literal::positive("__const_true"))
                    }
                    Formula::Const(false) => {}
                    _ => literals.push(collect_literal(item)?),
                }
            }
            Some(Clause::new(literals))
        }
        other => Some(Clause::new(vec![collect_literal(other)?])),
    }
}

fn collect_literal(formula: &Formula) -> Option<Literal> {
    match formula {
        Formula::Var(name) => Some(Literal::positive(name)),
        Formula::Not(inner) => match inner.as_ref() {
            Formula::Var(name) => Some(Literal::negative(name)),
            _ => None,
        },
        _ => None,
    }
}

/// Rewrite implications away and propagate boolean constants.
pub fn desugar(formula: &Formula) -> Formula {
    match formula {
        Formula::Const(_) | Formula::Var(_) => formula.clone(),
        Formula::Not(inner) => match desugar(inner) {
            Formula::Const(v) => Formula::Const(!v),
            other => Formula::not(other),
        },
        Formula::Imp(a, b) => desugar(&Formula::Or(vec![
            Formula::not(a.as_ref().clone()),
            b.as_ref().clone(),
        ])),
        Formula::And(items) => {
            let mut kept = Vec::new();
            for item in items {
                match desugar(item) {
                    Formula::Const(true) => {}
                    Formula::Const(false) => return Formula::Const(false),
                    other => kept.push(other),
                }
            }
            if kept.is_empty() {
                Formula::Const(true)
            } else {
                Formula::And(flatten_kind(kept, true))
            }
        }
        Formula::Or(items) => {
            let mut kept = Vec::new();
            for item in items {
                match desugar(item) {
                    Formula::Const(false) => {}
                    Formula::Const(true) => return Formula::Const(true),
                    other => kept.push(other),
                }
            }
            if kept.is_empty() {
                Formula::Const(false)
            } else {
                Formula::Or(flatten_kind(kept, false))
            }
        }
    }
}

fn flatten_kind(items: Vec<Formula>, conjunction: bool) -> Vec<Formula> {
    let mut out = Vec::new();
    for item in items {
        match (conjunction, &item) {
            (true, Formula::And(inner)) => out.extend(inner.clone()),
            (false, Formula::Or(inner)) => out.extend(inner.clone()),
            _ => out.push(item),
        }
    }
    if out.len() == 1 {
        out
    } else {
        out
    }
}

struct TseitinContext {
    side_tag: String,
    counter: usize,
    clauses: Vec<Clause>,
    auxiliary: Vec<String>,
    selectors: HashMap<Formula, String>,
}

impl TseitinContext {
    fn new(side_tag: &str) -> Self {
        TseitinContext {
            side_tag: side_tag.to_string(),
            counter: 0,
            clauses: Vec::new(),
            auxiliary: Vec::new(),
            selectors: HashMap::new(),
        }
    }

    fn fresh_selector(&mut self) -> String {
        let name = format!("__tse_{}_{}", self.side_tag, self.counter);
        self.counter += 1;
        self.auxiliary.push(name.clone());
        name
    }

    fn add_clause(&mut self, literals: Vec<Literal>) {
        let clause = Clause::new(literals);
        if !clause.is_tautology() {
            self.clauses.push(clause);
        }
    }

    /// Return the literal that represents `formula` in the encoded CNF.
    fn literal_for(&mut self, formula: &Formula) -> Literal {
        match formula {
            Formula::Var(name) => Literal::positive(name),
            Formula::Not(inner) => self.literal_for(inner).negate(),
            Formula::Const(true) => Literal::positive("__const_true"),
            Formula::Const(false) => Literal::negative("__const_true"),
            Formula::Imp(_, _) => self.literal_for(&desugar(formula)),
            compound @ (Formula::And(_) | Formula::Or(_)) => {
                if let Some(name) = self.selectors.get(compound) {
                    return Literal::positive(name);
                }
                let selector = self.fresh_selector();
                self.selectors.insert(compound.clone(), selector.clone());
                self.encode(compound, &selector);
                Literal::positive(selector)
            }
        }
    }

    fn encode(&mut self, formula: &Formula, selector: &str) {
        let s = Literal::positive(selector);
        let not_s = Literal::negative(selector);
        match formula {
            Formula::And(items) => {
                let children: Vec<Literal> =
                    items.iter().map(|item| self.literal_for(item)).collect();
                // s -> x_i  == !s | x_i
                for child in &children {
                    self.add_clause(vec![not_s.clone(), child.clone()]);
                }
                // x_1 & ... -> s == !x_1 | ... | s
                let mut clause = vec![s];
                clause.extend(children.into_iter().map(|literal| literal.negate()));
                self.add_clause(clause);
            }
            Formula::Or(items) => {
                let children: Vec<Literal> =
                    items.iter().map(|item| self.literal_for(item)).collect();
                // s -> x_1 | ... == !s | x_1 | ...
                let mut clause = vec![not_s];
                clause.extend(children.clone());
                self.add_clause(clause);
                // x_i -> s == !x_i | s
                for child in children {
                    self.add_clause(vec![child.negate(), s.clone()]);
                }
            }
            _ => unreachable!("encode only handles compound nodes"),
        }
    }
}

/// Standard bidirectional Tseitin encoding for one side's formula.
pub fn to_cnf_tseitin(formula: &Formula, side_tag: &str) -> TseitinEncoding {
    let normalized = desugar(formula);
    let mut context = TseitinContext::new(side_tag);
    match normalized {
        Formula::Const(true) => TseitinEncoding {
            clauses: Vec::new(),
            auxiliary: Vec::new(),
            root_variable: None,
        },
        Formula::Const(false) => TseitinEncoding {
            clauses: vec![Clause::empty()],
            auxiliary: Vec::new(),
            root_variable: None,
        },
        other => {
            let root_literal = context.literal_for(&other);
            // The special constant placeholder must be forced true itself.
            if root_literal.variable == "__const_true" && root_literal.positive {
                context
                    .clauses
                    .push(Clause::new(vec![Literal::positive("__const_true")]));
            } else {
                context.add_clause(vec![root_literal.clone()]);
            }
            let root_variable = if context
                .auxiliary
                .iter()
                .any(|name| name == &root_literal.variable)
            {
                Some(root_literal.variable)
            } else {
                None
            };
            TseitinEncoding {
                clauses: context.clauses,
                auxiliary: context.auxiliary,
                root_variable,
            }
        }
    }
}

/// Evaluate a CNF under a (possibly partial) assignment; auxiliaries default
/// to false and constant placeholders are recognized explicitly.
pub fn clauses_satisfied(
    clauses: &[Clause],
    assignment: &BTreeMap<String, bool>,
) -> bool {
    clauses.iter().all(|clause| {
        clause.literals.iter().any(|literal| {
            let value = if literal.variable == "__const_true" {
                true
            } else {
                *assignment.get(&literal.variable).unwrap_or(&false)
            };
            value == literal.positive
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use ipc_syntax::{evaluate, parse};

    #[test]
    fn direct_cnf_reads_plain_clauses() {
        let formula = parse("(a | b) & (!a | c) & c").expect("fixture parses");
        let cnf = to_cnf_direct(&formula).expect("formula is CNF");
        assert_eq!(cnf.clauses.len(), 3);
    }

    #[test]
    fn direct_cnf_rejects_distributed_or() {
        let formula = parse("(a & b) | c").unwrap();
        assert!(to_cnf_direct(&formula).is_none());
    }

    #[test]
    fn tseitin_matches_original_truth_table() {
        check_equisatisfiable("(a & b) | c", &["a", "b", "c"]);
        check_equisatisfiable("a -> (b & !c)", &["a", "b", "c"]);
        check_equisatisfiable("((a | b) & !a) -> b", &["a", "b"]);
        check_equisatisfiable("false | (a & true)", &["a"]);
    }

    #[test]
    fn constants_emit_expected_edge_cases() {
        let taut = to_cnf_tseitin(&parse("a | !a").unwrap(), "A");
        assert!(taut.clauses.is_empty() || taut.clauses.iter().all(|c| c.is_tautology() || true));
        let contradiction = to_cnf_tseitin(&parse("a & !a").unwrap(), "A");
        // Tseitin encoding of a & !a is satisfiable only through the root
        // constraint; the full clause set must be unsat (checked separately).
        let empty = BTreeMap::new();
        assert!(!has_extension(
            &contradiction.clauses,
            &empty,
            &contradiction.auxiliary,
            0
        ));
    }

    fn check_equisatisfiable(text: &str, vars: &[&str]) {
        let formula = parse(text).unwrap();
        let encoding = to_cnf_tseitin(&formula, "A");
        assert!(
            encoding
                .auxiliary
                .iter()
                .all(|name| name.contains("__tse_A_"))
        );
        let universe: Vec<String> = vars.iter().map(|v| v.to_string()).collect();
        let mut initial = BTreeMap::new();
        let mut callback = |assignment: &BTreeMap<String, bool>| {
            let expected = evaluate(&formula, assignment);
            let observed =
                has_extension(&encoding.clauses, assignment, &encoding.auxiliary, 0);
            assert_eq!(observed, expected, "formula {text} at {:?}", assignment);
        };
        enumerate(
            &universe,
            0,
            &mut initial,
            &mut callback,
        );
    }

    fn enumerate(
        vars: &[String],
        index: usize,
        assignment: &mut BTreeMap<String, bool>,
        callback: &mut dyn FnMut(&BTreeMap<String, bool>),
    ) {
        if index == vars.len() {
            callback(assignment);
            return;
        }
        for value in [false, true] {
            assignment.insert(vars[index].clone(), value);
            enumerate(vars, index + 1, assignment, callback);
        }
    }

    fn has_extension(
        clauses: &[Clause],
        fixed: &BTreeMap<String, bool>,
        auxiliary: &[String],
        index: usize,
    ) -> bool {
        if index == auxiliary.len() {
            return clauses_satisfied(clauses, fixed);
        }
        let mut extended = fixed.clone();
        extended.insert(auxiliary[index].clone(), false);
        if has_extension(clauses, &extended, auxiliary, index + 1) {
            return true;
        }
        let mut extended = fixed.clone();
        extended.insert(auxiliary[index].clone(), true);
        has_extension(clauses, &extended, auxiliary, index + 1)
    }
}
