//! Independent checker: brute-force truth-table enumeration, used to
//! cross-validate the reasoning core on small circuits. Shares no code
//! with the counting core beyond the syntax tree itself.

use crate::syntax::{Circuit, Var};
use num_bigint::BigUint;
use std::collections::BTreeSet;

/// Count satisfying assignments by exhaustive enumeration over `vars`.
/// Callers must keep `vars.len()` small (this is 2^n work).
pub fn brute_force_count(circuit: &Circuit, vars: &BTreeSet<Var>) -> BigUint {
    let vars: Vec<Var> = vars.iter().copied().collect();
    let n = vars.len();
    assert!(n <= 63, "brute force limited to 63 variables");
    let mut count = BigUint::from(0u64);
    for mask in 0u64..(1u64 << n) {
        let assignment = |v: Var| {
            vars.iter()
                .position(|&x| x == v)
                .map(|i| (mask >> i) & 1 == 1)
                .unwrap_or(false)
        };
        if circuit.eval(circuit.root, &assignment) {
            count += 1u64;
        }
    }
    count
}
