//! Type checker for Church-style STLC over locally nameless terms.
//!
//! Lambda abstractions carry their domain annotation (`Abs.param_ty`).
//! Successful checks return a full typing derivation as serializable data.

use std::collections::HashMap;

use stlc_proof::{Binding, DerivNode, Rule, TypingProof};
use stlc_syntax::db::DbTerm;
use stlc_syntax::Type;

use crate::error::TypeError;

pub struct Checker {
    free: HashMap<String, Type>,
    /// Bound assumptions, innermost binder at the tail; de Bruijn index `i`
    /// therefore resolves to element `len - 1 - i`.
    bound: Vec<Type>,
}

impl Checker {
    pub fn new(free_signature: impl IntoIterator<Item = Binding>) -> Self {
        Checker {
            free: free_signature
                .into_iter()
                .map(|b| (b.name, b.ty))
                .collect(),
            bound: Vec::new(),
        }
    }

    fn snapshot(&self) -> Vec<Binding> {
        let mut ctx: Vec<Binding> = self
            .free
            .iter()
            .map(|(name, ty)| Binding {
                name: name.clone(),
                ty: ty.clone(),
            })
            .collect();
        ctx.sort_by(|a, b| a.name.cmp(&b.name));
        for (depth, ty) in self.bound.iter().enumerate() {
            ctx.push(Binding {
                name: format!("__b{depth}"),
                ty: ty.clone(),
            });
        }
        ctx
    }

    fn node(
        &self,
        rule: Rule,
        term: DbTerm,
        ty: Type,
        children: Vec<DerivNode>,
        justification: impl Into<String>,
    ) -> DerivNode {
        DerivNode {
            rule,
            context: self.snapshot(),
            term,
            ty,
            children,
            justification: justification.into(),
        }
    }

    pub fn check(&mut self, term: &DbTerm) -> Result<(Type, DerivNode), TypeError> {
        match term {
            DbTerm::BVar { index } => {
                let depth = self.bound.len();
                let ty = self
                    .bound
                    .get(depth - 1 - *index)
                    .ok_or(TypeError::DanglingIndex {
                        index: *index,
                        depth,
                    })?
                    .clone();
                Ok((
                    ty.clone(),
                    self.node(
                        Rule::Var,
                        term.clone(),
                        ty,
                        vec![],
                        format!("bound variable index {index} resolves in context (depth {depth})"),
                    ),
                ))
            }
            DbTerm::FVar { name } => {
                let ty = self
                    .free
                    .get(name)
                    .ok_or_else(|| TypeError::UnknownFreeVar { name: name.clone() })?
                    .clone();
                Ok((
                    ty.clone(),
                    self.node(
                        Rule::Var,
                        term.clone(),
                        ty,
                        vec![],
                        format!("free variable `{name}` matches declared assumption"),
                    ),
                ))
            }
            DbTerm::BoolLit { value } => {
                let rule = if *value { Rule::BoolTrue } else { Rule::BoolFalse };
                Ok((
                    Type::Bool,
                    self.node(
                        rule,
                        term.clone(),
                        Type::Bool,
                        vec![],
                        "boolean literal axiom: true/false : Bool",
                    ),
                ))
            }
            DbTerm::Abs { param_ty, body } => {
                self.bound.push(param_ty.clone());
                let body_result = self.check(body);
                self.bound.pop();
                let (body_ty, body_node) = body_result?;
                let result = Type::Arrow {
                    domain: Box::new(param_ty.clone()),
                    codomain: Box::new(body_ty),
                };
                Ok((
                    result.clone(),
                    self.node(
                        Rule::Abs,
                        term.clone(),
                        result,
                        vec![body_node],
                        "abstraction introduction: bind domain assumption, read body type",
                    ),
                ))
            }
            DbTerm::App { func, arg } => {
                let (func_ty, func_node) = self.check(func)?;
                let (arg_ty, arg_node) = self.check(arg)?;
                match func_ty {
                    Type::Arrow { domain, codomain } => {
                        if *domain != arg_ty {
                            return Err(TypeError::DomainMismatch {
                                expected: *domain,
                                found: arg_ty,
                            });
                        }
                        let result = (*codomain).clone();
                        Ok((
                            result.clone(),
                            self.node(
                                Rule::App,
                                term.clone(),
                                result,
                                vec![func_node, arg_node],
                                "application elimination: argument type equals function domain",
                            ),
                        ))
                    }
                    other => Err(TypeError::ExpectedFunction { found: other }),
                }
            }
            DbTerm::If {
                cond,
                then,
                otherwise,
            } => {
                let (cond_ty, cond_node) = self.check(cond)?;
                if cond_ty != Type::Bool {
                    return Err(TypeError::IfGuardNotBool { found: cond_ty });
                }
                let (then_ty, then_node) = self.check(then)?;
                let (else_ty, else_node) = self.check(otherwise)?;
                if then_ty != else_ty {
                    return Err(TypeError::BranchMismatch {
                        then_ty,
                        else_ty,
                    });
                }
                Ok((
                    then_ty.clone(),
                    self.node(
                        Rule::If,
                        term.clone(),
                        then_ty,
                        vec![cond_node, then_node, else_node],
                        "if elimination: guard is Bool and both branches share a type",
                    ),
                ))
            }
        }
    }
}

/// Check `term` against an explicit free-variable signature and return the
/// complete proof record.
pub fn check_term(
    term: &DbTerm,
    free_signature: Vec<Binding>,
) -> Result<TypingProof, TypeError> {
    let mut checker = Checker::new(free_signature.clone());
    let (_, root) = checker.check(term)?;
    Ok(TypingProof {
        free_signature,
        root,
    })
}
