package tests

import (
	"testing"

	"simdc/internal/pipeline"
)

// Build a program that only reduces (no body stores needed). Use dedicated
// source strings to keep the language minimal.
const reduceOnly = `
input a[5]int64
output sum int64, prod int64, digits int64, touched[5]int64
for i := 0 .. len(a) {
  touched[i] = a[i]
}
for j := 0 .. len(a) { reduce sum += a[j] }
for k := 0 .. len(a) { reduce prod *= a[k] }
for m := 0 .. len(a) { reduce digits concat= a[m] }
`

func TestReductionOrderAscending(t *testing.T) {
	// concat computes (((((0*10+d0)*10+d1)...))), which is only the decimal
	// concat when every lane is a single digit and which is order-sensitive:
	// ascending order yields 12345, descending would yield 54321.
	req := pipeline.Request{
		RequestID: "req-reduce-010",
		Source:    reduceOnly,
		Width:     3, // 5 lanes -> batches 3 + 2, exercises tail in reduce input
		Arrays:    map[string][]int64{"a": {1, 2, 3, 4, 5}},
	}
	resp := pipeline.Execute(req)
	if resp.Verdict != "ACCEPT" || !resp.Diff.Match {
		t.Fatalf("verdict=%s diff=%+v issues=%+v", resp.Verdict, resp.Diff, resp.Issues)
	}
	sc := resp.Simd.Scalars
	if sc["sum"] != 15 {
		t.Fatalf("sum=%d want 15", sc["sum"])
	}
	if sc["prod"] != 120 {
		t.Fatalf("prod=%d want 120", sc["prod"])
	}
	if sc["digits"] != 12345 {
		t.Fatalf("digits=%d want 12345 (ascending left-fold)", sc["digits"])
	}
}

const shortCircuitSrc = `
input a[4]int64, d[4]int64
output out[4]int64
for i := 0 .. len(a) {
  if (i < 2 && a[i]/d[i] > 0) {
    out[i] = 1
  }
}
`

func TestShortCircuitMaskedDivZero(t *testing.T) {
	// Lanes 2,3 fail i<2 first, so a[i]/d[i] with d=0 must not be evaluated.
	req := pipeline.Request{
		RequestID: "req-shortcircuit-011",
		Source:    shortCircuitSrc,
		Width:     4,
		Arrays: map[string][]int64{
			"a": {6, 6, 9, 9},
			"d": {2, 3, 0, 0},
		},
	}
	resp := pipeline.Execute(req)
	if resp.Verdict != "ACCEPT" || !resp.Diff.Match {
		t.Fatalf("verdict=%s diff=%+v", resp.Verdict, resp.Diff)
	}
	assertSlice(t, "out", resp.Simd.Arrays[0], []int64{1, 1, 0, 0})
	if resp.Simd.Halted {
		t.Fatalf("short-circuit must mask div-by-zero: %+v", resp.Simd.Fault)
	}
}
