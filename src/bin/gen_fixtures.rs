//! `gen_fixtures`: deterministically generates the larger synthetic proof
//! fixtures under `fixtures/` (data generation stays inside the Rust
//! toolchain; no external data or runtimes involved).
//!
//! The handwritten small fixtures (valid, tampered, dangling, ...) are
//! maintained as files directly; this tool only produces the generated
//! chain proof used for resource-limit testing, plus a `.golden` file with
//! the expected verdict so tests do not rely on the checker to define the
//! expected answer.

use std::fmt::Write as _;
use std::fs;
use std::path::Path;

/// Build a chain proof over variables 1..=n:
/// axioms (x1), (~x1 v x2), (~x2 v x3), ..., (~x_{n-1} v x_n), (~x_n),
/// then resolve left to right until the empty clause.
fn chain_proof(n: u32) -> String {
    let mut out = String::new();
    writeln!(out, "# generated chain proof, {n} variables").unwrap();
    writeln!(out, "c 1 1 0").unwrap();
    for v in 2..=n {
        writeln!(out, "c {v} -{} {} 0", v - 1, v).unwrap();
    }
    writeln!(out, "c {} -{} 0", n + 1, n).unwrap();
    // Resolve the accumulated unit clause with the next axiom, left to right.
    let mut acc = 1u64;
    for v in 2..=n {
        let step = n as u64 + v as u64;
        writeln!(out, "r {step} {acc} {v} {} {v} 0", v - 1).unwrap();
        acc = step;
    }
    let last = n as u64 + n as u64 + 1;
    writeln!(out, "r {last} {acc} {} {n} 0", n + 1).unwrap();
    out
}

fn main() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures");
    fs::create_dir_all(&dir).expect("create fixtures dir");

    let proof = chain_proof(64);
    fs::write(dir.join("generated_chain.proof"), &proof).expect("write chain proof");
    // The chain derives the empty clause after 2*n + 1 records.
    fs::write(dir.join("generated_chain.golden"), "verified\nsteps=129\n")
        .expect("write golden file");

    println!("generated fixtures/generated_chain.proof (64 variables, 129 records)");
}
