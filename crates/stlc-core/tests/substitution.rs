//! Focused tests for capture avoidance, binding depth and normalization
//! mechanics — including the classic capture pitfall.

use stlc_core::subst::{beta_contract, shift, substitute};
use stlc_syntax::db::DbTerm;
use stlc_syntax::{parse, Type};

fn db(src: &str) -> DbTerm {
    stlc_syntax::db::to_locally_nameless(&parse::parse_term(src).unwrap())
}

fn arrow_bool() -> Type {
    Type::Arrow {
        domain: Box::new(Type::Bool),
        codomain: Box::new(Type::Bool),
    }
}

#[test]
fn beta_does_not_capture_free_indices_under_binders() {
    // (\x. (\y. x)) applied to a term whose representation, at the contract
    // site under two surrounding binders, contains a free reference that must
    // be shifted past the inner binder rather than captured.
    //
    // Concrete instance at top level:
    //   (\x: Bool. \y: Bool. x) applied to true must yield \y. true,
    // never \y. y (which would indicate capture).
    let body = db("lam y: Bool. x");
    // Note: x is free in the surface term; after conversion of the full
    // redex below it becomes the shifted argument.
    let _ = body;
    let redex = db("(lam x: Bool. lam y: Bool. x) true");
    let result = match redex {
        DbTerm::App { func, arg } => match *func {
            DbTerm::Abs { body, .. } => beta_contract(&body, &arg),
            _ => unreachable!(),
        },
        _ => unreachable!(),
    };
    // \y. true  (inner body refers to the outer binder's argument, which is
    // represented as index 1 *before* the outer lambda disappears).
    let expected = db("lam y: Bool. true");
    assert_eq!(result, expected);
}

#[test]
fn beta_under_surrounding_binder_preserves_outer_references() {
    // In context with one outer binder z:
    //   (\f:Bool->Bool. \b:Bool. f b) applied to f(#1 relative to b means
    // the outer f)... check by reducing `(\f. \b. f b) (\x. x)` under a
    // lambda: result \b. (\x.x) b -> \b. b ; index discipline verified via
    // full pipeline on a closed term instead:
    let term =
        db("(lam f: Bool -> Bool. lam b: Bool. f b) (lam x: Bool. x)");
    let reduced = match term {
        DbTerm::App { func, arg } => match *func {
            DbTerm::Abs { body, .. } => beta_contract(&body, &arg),
            _ => unreachable!(),
        },
        _ => unreachable!(),
    };
    // (\b. (\x.x) b)
    let expected = db("lam b: Bool. (lam x: Bool. x) b");
    assert_eq!(reduced, expected);
}

#[test]
fn shift_only_moves_indices_at_or_above_cutoff() {
    // #0 #1 under one binder with cutoff 1: #0 stays, #1 moves.
    let term = DbTerm::App {
        func: Box::new(DbTerm::BVar { index: 0 }),
        arg: Box::new(DbTerm::Abs {
            param_ty: Type::Bool,
            body: Box::new(DbTerm::BVar { index: 1 }),
        }),
    };
    let shifted = shift(&term, 1);
    // Outer #0 (cutoff 0 at App level) -> #1; inside Abs cutoff increments so
    // #1 (>= 1) -> #2.
    let expected = DbTerm::App {
        func: Box::new(DbTerm::BVar { index: 1 }),
        arg: Box::new(DbTerm::Abs {
            param_ty: Type::Bool,
            body: Box::new(DbTerm::BVar { index: 2 }),
        }),
    };
    assert_eq!(shifted, expected);
}

#[test]
fn substitute_respects_depth_and_avoids_capture() {
    // Inside an Abs the cutoff rises: an outer free index maps to a higher
    // number, and the bound #0 must never be touched (capture avoidance).
    // Term `lam. (#0 #2)` replacing outer index 1 (= #2 inside the binder)
    // by true yields `lam. (#0 true)`; #0 stays bound.
    let term = DbTerm::Abs {
        param_ty: Type::Bool,
        body: Box::new(DbTerm::App {
            func: Box::new(DbTerm::BVar { index: 0 }),
            arg: Box::new(DbTerm::BVar { index: 2 }),
        }),
    };
    // Replace outer free index 1 (seen as index 2 inside the Abs) by true:
    let out = substitute(1, &DbTerm::BoolLit { value: true }, &term);
    let expected = DbTerm::Abs {
        param_ty: Type::Bool,
        body: Box::new(DbTerm::App {
            func: Box::new(DbTerm::BVar { index: 0 }),
            arg: Box::new(DbTerm::BoolLit { value: true }),
        }),
    };
    assert_eq!(out, expected);
}

#[test]
fn deeply_nested_identity_type_roundtrips() {
    // Build A = Bool -> Bool -> ... -> Bool (depth 200 arrows), then check
    // \x:A.x is accepted by confirming parse + conversion preserves depth.
    let mut ty_src = String::from("Bool");
    for _ in 0..200 {
        ty_src = format!("Bool -> {ty_src}");
    }
    let src = format!("lam x: {ty_src}. x");
    let term = parse::parse_term(&src).unwrap();
    let dbt = stlc_syntax::db::to_locally_nameless(&term);
    match dbt {
        DbTerm::Abs { param_ty, body } => {
            let mut count = 1usize;
            let mut cur = param_ty;
            while let Type::Arrow { domain: _, codomain } = cur {
                count += 1;
                cur = *codomain;
            }
            assert_eq!(count, 201);
            assert_eq!(*body, DbTerm::BVar { index: 0 });
        }
        _ => panic!("expected abstraction"),
    }
}

#[test]
fn deep_left_associated_application_reduces_exact_count() {
    // n wrappers (id) applied to true -> exactly n beta steps.
    let n = 300usize;
    let mut src = String::from("true");
    for _ in 0..n {
        src = format!("(lam x: Bool. x) ({src})");
    }
    let mut term = db(&src);
    let mut steps = 0;
    loop {
        let next = innermost_step(&term);
        match next {
            Some(t) => {
                term = t;
                steps += 1;
            }
            None => break,
        }
    }
    assert_eq!(steps, n);
    assert_eq!(term, DbTerm::BoolLit { value: true });
}

// A tiny independent normalizer (not using stlc_core::Normalizer) used to
// cross-check step counts; written in the most straightforward way.
fn innermost_step(term: &DbTerm) -> Option<DbTerm> {
    match term {
        DbTerm::BVar { .. } | DbTerm::FVar { .. } | DbTerm::BoolLit { .. } => None,
        DbTerm::Abs { param_ty, body } => innermost_step(body).map(|b| DbTerm::Abs {
            param_ty: param_ty.clone(),
            body: Box::new(b),
        }),
        DbTerm::App { func, arg } => {
            if let Some(f2) = innermost_step(func) {
                return Some(DbTerm::App {
                    func: Box::new(f2),
                    arg: arg.clone(),
                });
            }
            if let Some(a2) = innermost_step(arg) {
                return Some(DbTerm::App {
                    func: func.clone(),
                    arg: Box::new(a2),
                });
            }
            match func.as_ref() {
                DbTerm::Abs { body, .. } => Some(beta_contract(body, arg)),
                _ => None,
            }
        }
        DbTerm::If {
            cond,
            then,
            otherwise,
        } => {
            if let Some(c2) = innermost_step(cond) {
                return Some(DbTerm::If {
                    cond: Box::new(c2),
                    then: then.clone(),
                    otherwise: otherwise.clone(),
                });
            }
            match cond.as_ref() {
                DbTerm::BoolLit { value } => {
                    if *value {
                        Some((**then).clone())
                    } else {
                        Some((**otherwise).clone())
                    }
                }
                _ => None,
            }
        }
    }
}
