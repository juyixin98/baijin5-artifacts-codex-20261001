//! Shared test fixtures: deterministic synthetic columns, an independent
//! reference decoder (written separately from the codec kernel), and a
//! structured test logger.
//!
//! The reference decoder exists so that expected answers in tests are NOT
//! produced by the kernel under test. It parses the same documented format
//! (docs/FORMAT.md) with its own naive bit-by-bit logic.

#![allow(dead_code)]

/// Deterministic xorshift64* generator — no external deps, stable across runs.
pub struct XorShift(u64);

impl XorShift {
    pub fn new(seed: u64) -> Self {
        XorShift(seed.max(1))
    }
    pub fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545F4914F6CDD1D)
    }
    pub fn below(&mut self, bound: u64) -> u64 {
        self.next() % bound
    }
}

/// FNV-1a digest of a column, used to tie log lines to inputs.
pub fn digest(values: &[u64]) -> u64 {
    let mut h = 0xcbf29ce484222325u64;
    for v in values {
        for b in v.to_le_bytes() {
            h ^= b as u64;
            h = h.wrapping_mul(0x100000001b3);
        }
    }
    h
}

/// Structured test logger. Every line carries the run identity, case name,
/// and a computation step, plus the verdict basis when a check completes.
pub struct TestLog {
    run: String,
    case: String,
}

impl TestLog {
    pub fn new(case: &str) -> Self {
        let run = std::env::var("RIB_TEST_RUN").unwrap_or_else(|_| {
            format!("pid{}-{}", std::process::id(), case)
        });
        let log = TestLog {
            run,
            case: case.to_string(),
        };
        log.step(
            "begin",
            &format!(
                "crate={} format_version={}",
                env!("CARGO_PKG_VERSION"),
                rib::format::VERSION
            ),
        );
        log
    }

    pub fn step(&self, step: &str, msg: &str) {
        println!(
            "[run={} case={} step={}] {}",
            self.run, self.case, step, msg
        );
    }

    pub fn verdict(&self, ok: bool, basis: &str) {
        self.step(
            "verdict",
            &format!("{} basis={}", if ok { "PASS" } else { "FAIL" }, basis),
        );
    }
}

/// Alternating long repeats and short variations — exercises mode switching.
pub fn alternating_column(seed: u64, cycles: usize) -> Vec<u64> {
    let mut rng = XorShift::new(seed);
    let mut out = Vec::new();
    for _ in 0..cycles {
        let run_val = rng.below(1 << 20);
        let run_len = 8 + rng.below(500) as usize;
        out.extend(std::iter::repeat_n(run_val, run_len));
        let lit_len = 1 + rng.below(7) as usize;
        for _ in 0..lit_len {
            out.push(rng.below(1 << 12));
        }
    }
    out
}

/// Column whose values cross bit-width boundaries (2^k - 1, 2^k, 2^k + 1).
pub fn bitwidth_crossing_column() -> Vec<u64> {
    let mut out = Vec::new();
    for k in [0u32, 1, 7, 8, 15, 16, 31, 32, 63] {
        let base = 1u64 << k;
        out.push(base - 1);
        out.push(base);
        out.push(base + 1);
    }
    out.push(u64::MAX);
    out.push(0);
    out
}

/// Column with a non-integral tail: value_count % 8 != 0 in the final
/// BITPACK block, so tail-group padding must not leak into the output.
pub fn tail_partial_column() -> Vec<u64> {
    let mut out = vec![0xABu64; 13]; // short of min_run? no: 13 >= 8 -> RLE
    out.extend_from_slice(&[1, 2, 3, 4, 5]); // 5 literals -> partial group
    out
}

/// Independent reference decoder. Naive, bit-by-bit, bounds-checked with
/// plain Option semantics; shares no code with the kernel.
pub fn reference_decode(bytes: &[u8]) -> Option<Vec<u64>> {
    let mut values = Vec::new();
    let mut pos = 0usize;
    while pos < bytes.len() {
        if bytes.len() - pos < 16 {
            return None;
        }
        if &bytes[pos..pos + 4] != b"RIB1" || bytes[pos + 4] != 1 || bytes[pos + 7] != 0 {
            return None;
        }
        let mode = bytes[pos + 5];
        let width = bytes[pos + 6] as usize;
        if width > 64 {
            return None;
        }
        let count = u32::from_le_bytes(bytes[pos + 8..pos + 12].try_into().ok()?) as usize;
        let plen = u32::from_le_bytes(bytes[pos + 12..pos + 16].try_into().ok()?) as usize;
        let payload_start = pos + 16;
        if bytes.len() - payload_start < plen {
            return None;
        }
        let payload = &bytes[payload_start..payload_start + plen];
        match mode {
            0 => {
                // Naive bit-by-bit reader over absolute bit positions.
                let get_bit = |i: usize| -> u64 { (payload[i / 8] as u64 >> (i % 8)) & 1 };
                for v in 0..count {
                    let mut value = 0u64;
                    for b in 0..width {
                        let bit_pos = v * width + b;
                        if bit_pos >= plen * 8 {
                            return None;
                        }
                        value |= get_bit(bit_pos) << b;
                    }
                    values.push(value);
                }
            }
            1 => {
                let vbytes = width.div_ceil(8);
                let mut off = 0usize;
                let mut produced = 0usize;
                while produced < count {
                    if off + 4 + vbytes > plen {
                        return None;
                    }
                    let run_len =
                        u32::from_le_bytes(payload[off..off + 4].try_into().ok()?) as usize;
                    if run_len == 0 {
                        return None;
                    }
                    let mut value = 0u64;
                    for i in 0..vbytes {
                        value |= (payload[off + 4 + i] as u64) << (8 * i);
                    }
                    for _ in 0..run_len {
                        values.push(value);
                    }
                    produced += run_len;
                    off += 4 + vbytes;
                }
                if produced != count || off != plen {
                    return None;
                }
            }
            _ => return None,
        }
        pos = payload_start + plen;
    }
    Some(values)
}
