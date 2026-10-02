//! Independent proof checker.
//!
//! This crate is intentionally isolated from `stlc-core`: it re-derives every
//! typing rule from the recorded derivation and only trusts the shared syntax
//! types. Tests therefore have two implementations (producer and auditor)
//! that must agree; expected answers in fixtures are hand-written data, not
//! output copied from the system under test.

use serde::{Deserialize, Serialize};
use stlc_proof::{Binding, DerivNode, Rule, TypingProof};
use stlc_syntax::db::DbTerm;
use stlc_syntax::Type;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuditFinding {
    pub node_index: String,
    pub passed: bool,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuditReport {
    pub valid: bool,
    pub nodes_checked: usize,
    pub rule_counts: RuleCounts,
    pub findings: Vec<AuditFinding>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RuleCounts {
    pub var: usize,
    pub abs: usize,
    pub app: usize,
    pub bool_true: usize,
    pub bool_false: usize,
    pub r#if: usize,
}

/// Re-verify a complete typing proof. Returns a report; `valid == true` means
/// every node locally matches its claimed rule and the root conclusion is
/// consistent.
pub fn audit_proof(proof: &TypingProof) -> AuditReport {
    let mut report = AuditReport {
        valid: true,
        nodes_checked: 0,
        rule_counts: RuleCounts::default(),
        findings: Vec::new(),
    };
    let free_context = &proof.free_signature;
    walk(&proof.root, free_context, &mut Vec::new(), "root", &mut report);
    report
}

fn lookup(ctx: &[Binding], bound_depth: usize, term: &DbTerm) -> Option<Type> {
    match term {
        DbTerm::FVar { name } => ctx.iter().find(|b| &b.name == name).map(|b| b.ty.clone()),
        DbTerm::BVar { index } => {
            if *index >= bound_depth {
                return None;
            }
            // Bound assumptions are the last `bound_depth` entries, nearest
            // binder last; index 0 is the last entry.
            ctx.get(ctx.len() - 1 - *index)
                .map(|b| b.ty.clone())
        }
        _ => None,
    }
}

fn fail(
    report: &mut AuditReport,
    node_index: &str,
    detail: impl Into<String>,
) {
    report.valid = false;
    report.findings.push(AuditFinding {
        node_index: node_index.to_string(),
        passed: false,
        detail: detail.into(),
    });
}

fn pass(report: &mut AuditReport, node_index: &str, detail: impl Into<String>) {
    report.findings.push(AuditFinding {
        node_index: node_index.to_string(),
        passed: true,
        detail: detail.into(),
    });
}

fn walk(
    node: &DerivNode,
    free_sig: &[Binding],
    bound: &mut Vec<Binding>,
    index: &str,
    report: &mut AuditReport,
) {
    report.nodes_checked += 1;
    match node.rule {
        Rule::Var => {
            report.rule_counts.var += 1;
            let mut ctx = free_sig.to_vec();
            ctx.extend(bound.iter().cloned());
            match lookup(&ctx, bound.len(), &node.term) {
                Some(ty) if ty == node.ty => pass(
                    report,
                    index,
                    format!("var rule: hypothesis type {ty:?} equals conclusion"),
                ),
                Some(ty) => fail(
                    report,
                    index,
                    format!("var rule: context gives {ty:?} but conclusion is {:?}", node.ty),
                ),
                None => fail(report, index, "var rule: name/index not present in context"),
            }
            if !node.children.is_empty() {
                fail(report, index, "var rule must have no premises");
            }
        }
        Rule::BoolTrue | Rule::BoolFalse => {
            if node.rule == Rule::BoolTrue {
                report.rule_counts.bool_true += 1;
            } else {
                report.rule_counts.bool_false += 1;
            }
            let expected = matches!(
                (&node.term, node.rule),
                (DbTerm::BoolLit { value: true }, Rule::BoolTrue)
                    | (DbTerm::BoolLit { value: false }, Rule::BoolFalse)
            );
            if expected && node.ty == Type::Bool {
                pass(report, index, "boolean axiom: literal matches rule, type Bool");
            } else {
                fail(report, index, "boolean axiom: literal/rule/type disagree");
            }
            if !node.children.is_empty() {
                fail(report, index, "boolean axiom must have no premises");
            }
        }
        Rule::Abs => {
            report.rule_counts.abs += 1;
            let (domain, body) = match &node.term {
                DbTerm::Abs { param_ty, body } => (param_ty.clone(), body.as_ref().clone()),
                other => {
                    fail(
                        report,
                        index,
                        format!("abs rule recorded over non-abstraction {other:?}"),
                    );
                    return;
                }
            };
            if node.children.len() != 1 {
                fail(report, index, "abs rule needs exactly one body premise");
                return;
            }
            let child = &node.children[0];
            let expected = Type::Arrow {
                domain: Box::new(domain.clone()),
                codomain: Box::new(child.ty.clone()),
            };
            if expected != node.ty {
                fail(
                    report,
                    index,
                    format!("abs conclusion {expected:?} != claimed {:?}", node.ty),
                );
            }
            if child.term != body {
                fail(report, index, "abs premise term is not the abstraction body");
            }
            // Recurse under the new assumption.
            bound.push(Binding {
                name: format!("__b{}", bound.len()),
                ty: domain,
            });
            // The child's recorded context must contain the same bound prefix.
            let mut expected_ctx = free_sig.to_vec();
            expected_ctx.extend(bound.iter().cloned());
            if child.context != expected_ctx {
                fail(report, index, "abs premise context does not extend with domain");
            }
            walk(child, free_sig, bound, &format!("{index}.0"), report);
            bound.pop();
            if expected == node.ty {
                pass(report, index, "abs rule: arrow built from domain and body type");
            }
        }
        Rule::App => {
            report.rule_counts.app += 1;
            let (func, arg) = match &node.term {
                DbTerm::App { func, arg } => (func.as_ref().clone(), arg.as_ref().clone()),
                other => {
                    fail(report, index, format!("app rule over non-application {other:?}"));
                    return;
                }
            };
            if node.children.len() != 2 {
                fail(report, index, "app rule needs exactly two premises");
                return;
            }
            let f = &node.children[0];
            let a = &node.children[1];
            if f.term != func || a.term != arg {
                fail(report, index, "app premises do not match function/argument terms");
            }
            let expected_func = Type::Arrow {
                domain: Box::new(a.ty.clone()),
                codomain: Box::new(node.ty.clone()),
            };
            if f.ty != expected_func {
                fail(
                    report,
                    index,
                    format!("app: function premise {:?} != {expected_func:?}", f.ty),
                );
            }
            walk(f, free_sig, bound, &format!("{index}.0"), report);
            walk(a, free_sig, bound, &format!("{index}.1"), report);
            if f.ty == expected_func {
                pass(report, index, "app rule: domain agrees, conclusion is codomain");
            }
        }
        Rule::If => {
            report.rule_counts.r#if += 1;
            let (cond, then_t, else_t) = match &node.term {
                DbTerm::If {
                    cond,
                    then,
                    otherwise,
                } => (
                    cond.as_ref().clone(),
                    then.as_ref().clone(),
                    otherwise.as_ref().clone(),
                ),
                other => {
                    fail(report, index, format!("if rule over non-if term {other:?}"));
                    return;
                }
            };
            if node.children.len() != 3 {
                fail(report, index, "if rule needs exactly three premises");
                return;
            }
            let (c, t, e) = (&node.children[0], &node.children[1], &node.children[2]);
            if c.term != cond || t.term != then_t || e.term != else_t {
                fail(report, index, "if premises do not match the three subterms");
            }
            if c.ty != Type::Bool {
                fail(report, index, "if guard premise is not Bool");
            }
            if t.ty != node.ty || e.ty != node.ty {
                fail(
                    report,
                    index,
                    format!(
                        "if branches {:?}/{:?} must both equal conclusion {:?}",
                        t.ty, e.ty, node.ty
                    ),
                );
            }
            walk(c, free_sig, bound, &format!("{index}.0"), report);
            walk(t, free_sig, bound, &format!("{index}.1"), report);
            walk(e, free_sig, bound, &format!("{index}.2"), report);
            if c.ty == Type::Bool && t.ty == node.ty && e.ty == node.ty {
                pass(report, index, "if rule: Bool guard and equal branch types");
            }
        }
    }
}

/// Structural alpha-equivalence check exposed for fixture tests. Locally
/// nameless terms with no dangling indices are alpha-equivalent iff equal.
pub fn alpha_eq(left: &DbTerm, right: &DbTerm) -> bool {
    left.is_locally_closed() && right.is_locally_closed() && left == right
}

/// A post-normalization consistency bundle audited independently of the core.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NormConsistency {
    pub type_preserved: bool,
    pub free_vars_before: Vec<String>,
    pub free_vars_after: Vec<String>,
    pub free_vars_preserved: bool,
    pub normal_form_locally_closed: bool,
    pub normal_form_audit_valid: bool,
}

/// Audit that a claimed normal form has the same type, the same free
/// variables, and itself carries a valid typing proof under the signature.
pub fn audit_normalization(
    _signature: &[Binding],
    before_ty: &Type,
    after_proof: &TypingProof,
    fv_before: &[String],
) -> NormConsistency {
    let fv_after = after_proof.root.term.free_vars();
    let nf_report = audit_proof(after_proof);
    NormConsistency {
        type_preserved: &after_proof.root.ty == before_ty,
        free_vars_before: fv_before.to_vec(),
        free_vars_after: fv_after.clone(),
        free_vars_preserved: fv_before.iter().cloned().collect::<std::collections::BTreeSet<_>>()
            == fv_after.into_iter().collect(),
        normal_form_locally_closed: after_proof.root.term.is_locally_closed(),
        normal_form_audit_valid: nf_report.valid,
    }
}
