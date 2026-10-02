//! Independent-auditor tests with hand-constructed derivation trees,
//! including deliberately corrupted proofs that must be rejected. Expected
//! values are written by hand, never produced by stlc-core.

use stlc_audit::{alpha_eq, audit_proof};
use stlc_proof::{Binding, DerivNode, Rule, TypingProof};
use stlc_syntax::db::DbTerm;
use stlc_syntax::{parse, Type};

fn b(name: &str, ty: Type) -> Binding {
    Binding {
        name: name.to_string(),
        ty,
    }
}

fn leaf(rule: Rule, ctx: Vec<Binding>, term: DbTerm, ty: Type, why: &str) -> DerivNode {
    DerivNode {
        rule,
        context: ctx,
        term,
        ty,
        children: vec![],
        justification: why.to_string(),
    }
}

#[test]
fn accepts_a_hand_built_identity_proof() {
    // x:Bool |- x : Bool ;  |- lam x:Bool. x : Bool->Bool
    let xbind = b("__b0", Type::Bool);
    let var_node = leaf(
        Rule::Var,
        vec![xbind],
        DbTerm::BVar { index: 0 },
        Type::Bool,
        "x assumed",
    );
    let root = DerivNode {
        rule: Rule::Abs,
        context: vec![],
        term: DbTerm::Abs {
            param_ty: Type::Bool,
            body: Box::new(DbTerm::BVar { index: 0 }),
        },
        ty: Type::Arrow {
            domain: Box::new(Type::Bool),
            codomain: Box::new(Type::Bool),
        },
        children: vec![var_node],
        justification: "abs intro".to_string(),
    };
    let report = audit_proof(&TypingProof {
        free_signature: vec![],
        root,
    });
    assert!(report.valid, "{report:?}");
    assert_eq!(report.nodes_checked, 2);
}

#[test]
fn rejects_a_proof_whose_app_conclusion_is_faked() {
    // Claim `true false : Bool` with child types Bool/Bool: the function
    // premise is not an arrow, so the App rule cannot hold.
    let f_node = leaf(
        Rule::BoolTrue,
        vec![],
        DbTerm::BoolLit { value: true },
        Type::Bool,
        "true : Bool",
    );
    let a_node = leaf(
        Rule::BoolFalse,
        vec![],
        DbTerm::BoolLit { value: false },
        Type::Bool,
        "false : Bool",
    );
    let root = DerivNode {
        rule: Rule::App,
        context: vec![],
        term: DbTerm::App {
            func: Box::new(DbTerm::BoolLit { value: true }),
            arg: Box::new(DbTerm::BoolLit { value: false }),
        },
        ty: Type::Bool,
        children: vec![f_node, a_node],
        justification: "corrupt".to_string(),
    };
    let report = audit_proof(&TypingProof {
        free_signature: vec![],
        root,
    });
    assert!(!report.valid, "Bool is not a function; auditor must reject: {report:?}");
}

#[test]
fn rejects_true_axiom_over_false_literal() {
    let node = leaf(
        Rule::BoolTrue,
        vec![],
        DbTerm::BoolLit { value: false },
        Type::Bool,
        "mislabeled",
    );
    let report = audit_proof(&TypingProof {
        free_signature: vec![],
        root: node,
    });
    assert!(!report.valid);
}

#[test]
fn rejects_var_whose_context_lacks_the_binding() {
    let node = leaf(
        Rule::Var,
        vec![],
        DbTerm::BVar { index: 0 },
        Type::Bool,
        "dangling",
    );
    assert!(!audit_proof(&TypingProof {
        free_signature: vec![],
        root: node
    })
    .valid);
}

#[test]
fn alpha_equivalence_is_structural_after_de_bruijn_conversion() {
    // Hand fact: different names, same term; changed annotation, different term.
    let id_x = parse::parse_term("lam x: Bool. x").unwrap();
    let id_y = parse::parse_term("lam y: Bool. y").unwrap();
    let id_fun = parse::parse_term("lam x: Bool -> Bool. x").unwrap();
    assert!(alpha_eq(
        &stlc_syntax::db::to_locally_nameless(&id_x),
        &stlc_syntax::db::to_locally_nameless(&id_y),
    ));
    assert!(!alpha_eq(
        &stlc_syntax::db::to_locally_nameless(&id_x),
        &stlc_syntax::db::to_locally_nameless(&id_fun),
    ));
}

#[test]
fn dangling_indices_are_not_alpha_equivalent() {
    let dangling = DbTerm::Abs {
        param_ty: Type::Bool,
        body: Box::new(DbTerm::BVar { index: 5 }),
    };
    assert!(!alpha_eq(&dangling, &dangling));
}
