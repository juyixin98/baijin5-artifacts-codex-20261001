//! Parenthesis-precise pretty printing. Round-tripping through parser yields
//! an alpha/structurally equivalent term.

use crate::db::DbTerm;
use crate::{Term, Type};

// Precedence levels (higher binds tighter).
const TY_ARROW: u8 = 1;

const TERM_LAM: u8 = 1;
const TERM_APP: u8 = 2;
const TERM_ATOM: u8 = 3;

pub fn type_to_string(ty: &Type) -> String {
    let mut s = String::new();
    write_type(ty, 0, &mut s);
    s
}

fn write_type(ty: &Type, ctx: u8, out: &mut String) {
    match ty {
        Type::Bool => out.push_str("Bool"),
        Type::Base { name } => out.push_str(name),
        Type::Arrow { domain, codomain } => {
            if ctx > TY_ARROW {
                out.push('(');
            }
            write_type(domain, TY_ARROW + 1, out);
            out.push_str(" -> ");
            write_type(codomain, TY_ARROW, out);
            if ctx > TY_ARROW {
                out.push(')');
            }
        }
    }
}

pub fn term_to_string(term: &Term) -> String {
    let mut s = String::new();
    write_term(term, 0, &mut s);
    s
}

fn write_term(term: &Term, ctx: u8, out: &mut String) {
    match term {
        Term::Var { name } => out.push_str(name),
        Term::BoolLit { value } => out.push_str(if *value { "true" } else { "false" }),
        Term::Abs {
            param,
            param_ty,
            body,
        } => {
            if ctx > TERM_LAM {
                out.push('(');
            }
            out.push_str("lam ");
            out.push_str(param);
            out.push_str(": ");
            write_type(param_ty, 0, out);
            out.push_str(". ");
            write_term(body, TERM_LAM, out);
            if ctx > TERM_LAM {
                out.push(')');
            }
        }
        Term::App { func, arg } => {
            if ctx > TERM_APP {
                out.push('(');
            }
            write_term(func, TERM_APP, out);
            out.push(' ');
            write_term(arg, TERM_ATOM, out);
            if ctx > TERM_APP {
                out.push(')');
            }
        }
        Term::If {
            cond,
            then,
            otherwise,
        } => {
            if ctx > TERM_LAM {
                out.push('(');
            }
            out.push_str("if ");
            write_term(cond, 0, out);
            out.push_str(" then ");
            write_term(then, 0, out);
            out.push_str(" else ");
            write_term(otherwise, 0, out);
            if ctx > TERM_LAM {
                out.push(')');
            }
        }
    }
}

/// Print a de Bruijn term. Bound variables render as `#<index>`, free
/// variables by name. Useful in audit logs where binders are anonymous.
pub fn db_to_string(term: &DbTerm) -> String {
    let mut s = String::new();
    write_db(term, 0, &mut s);
    s
}

fn write_db(term: &DbTerm, ctx: u8, out: &mut String) {
    match term {
        DbTerm::BVar { index } => {
            out.push('#');
            out.push_str(&index.to_string());
        }
        DbTerm::FVar { name } => out.push_str(name),
        DbTerm::BoolLit { value } => out.push_str(if *value { "true" } else { "false" }),
        DbTerm::Abs { body, .. } => {
            if ctx > TERM_LAM {
                out.push('(');
            }
            out.push_str("lam. ");
            write_db(body, TERM_LAM, out);
            if ctx > TERM_LAM {
                out.push(')');
            }
        }
        DbTerm::App { func, arg } => {
            if ctx > TERM_APP {
                out.push('(');
            }
            write_db(func, TERM_APP, out);
            out.push(' ');
            write_db(arg, TERM_ATOM, out);
            if ctx > TERM_APP {
                out.push(')');
            }
        }
        DbTerm::If {
            cond,
            then,
            otherwise,
        } => {
            if ctx > TERM_LAM {
                out.push('(');
            }
            out.push_str("if ");
            write_db(cond, 0, out);
            out.push_str(" then ");
            write_db(then, 0, out);
            out.push_str(" else ");
            write_db(otherwise, 0, out);
            if ctx > TERM_LAM {
                out.push(')');
            }
        }
    }
}
