// Package tests holds integration tests that exercise the full pipeline.
// Expected concrete results are hand-computed; the scalar reference is an
// independent AST evaluator (internal/reference), never the SIMD core under
// test.
package tests

import (
	"testing"

	"simdc/internal/pipeline"
)

// maskDivSource: lane i computes a[i]/d[i] only when t[i] != 0. Lane 1 has
// d[1]=0 but t[1]=0 (must stay masked -> no trap). Lane 4 (tail of a width-4
// run) is active; lane 5 does not exist (len=5).
const maskDivSource = `
input a[5]int64, d[5]int64, t[5]int64
output out[5]int64
for i := 0 .. len(a) {
  if (t[i] != 0) {
    out[i] = a[i] / d[i]
  }
}
`

func TestMaskedDivisionAndTail(t *testing.T) {
	req := pipeline.Request{
		RequestID: "req-mask-div-001",
		Source:    maskDivSource,
		Width:     4,
		Arrays: map[string][]int64{
			"a": {20, 7, 9, 16, 10},
			"d": {2, 0, 3, 4, 5},
			"t": {1, 0, 1, 1, 1},
		},
	}
	resp := pipeline.Execute(req)
	if resp.Verdict != "ACCEPT" {
		t.Fatalf("verdict=%s issues=%+v", resp.Verdict, resp.Issues)
	}
	if resp.Diff == nil || !resp.Diff.Match {
		t.Fatalf("diff mismatch: %+v", resp.Diff)
	}
	if resp.Reference == nil || resp.Simd == nil {
		t.Fatal("missing result views")
	}
	got := resp.Simd.Arrays[0]
	want := []int64{10, 0, 3, 4, 2}
	assertSlice(t, "out", got, want)
	if resp.Simd.Halted {
		t.Fatalf("unexpected fault: %+v", resp.Simd.Fault)
	}
	// Event correlation must carry the request id and key state.
	if len(resp.Events) < 2 || resp.Events[0].RequestID != "req-mask-div-001" {
		t.Fatalf("events not correlated: %+v", resp.Events)
	}
}

// gatherSource: active lanes read b[idx[i]]; masked lanes must not perform
// the out-of-range gather.
const gatherSource = `
input a[6]int64, b[4]int64, idx[6]int64, t[6]int64
output out[6]int64
for i := 0 .. len(a) {
  if (t[i] != 0) {
    out[i] = b[idx[i]] + a[i]
  }
}
`

func TestMaskedOutOfBoundsGather(t *testing.T) {
	req := pipeline.Request{
		RequestID: "req-mask-oob-002",
		Source:    gatherSource,
		Width:     4,
		Arrays: map[string][]int64{
			"a":   {1, 2, 3, 4, 5, 6},
			"b":   {10, 20, 30, 40},
			"idx": {0, 99, 1, -7, 3, 2},
			"t":   {1, 0, 1, 0, 1, 1},
		},
	}
	resp := pipeline.Execute(req)
	if resp.Verdict != "ACCEPT" || !resp.Diff.Match {
		t.Fatalf("verdict=%s diff=%+v issues=%+v", resp.Verdict, resp.Diff, resp.Issues)
	}
	// idx=99 (lane1) and idx=-7 (lane3) are masked off by t.
	want := []int64{11, 0, 23, 0, 45, 36}
	assertSlice(t, "out", resp.Simd.Arrays[0], want)
	if resp.Simd.Halted {
		t.Fatalf("masked OOB must not fault, got %+v", resp.Simd.Fault)
	}
}

func TestActiveDivisionZeroFaultCategory(t *testing.T) {
	req := pipeline.Request{
		RequestID: "req-active-divz-003",
		Source:    maskDivSource,
		Width:     2,
		Arrays: map[string][]int64{
			"a": {20, 7, 9, 16, 10},
			"d": {2, 1, 0, 4, 5}, // lane 2 active divisor zero
			"t": {1, 1, 1, 1, 1},
		},
	}
	resp := pipeline.Execute(req)
	if resp.Verdict != "ACCEPT" {
		t.Fatalf("verdict=%s issues=%+v", resp.Verdict, resp.Issues)
	}
	if !resp.Simd.Halted || resp.Simd.Fault.Code != "DIV_BY_ZERO" {
		t.Fatalf("want active DIV_BY_ZERO, got halted=%v fault=%+v", resp.Simd.Halted, resp.Simd.Fault)
	}
	if resp.Simd.Fault.GlobalIdx != 2 {
		t.Fatalf("fault index=%d want 2", resp.Simd.Fault.GlobalIdx)
	}
	if resp.Simd.Fault.Stage != "BODY" {
		t.Fatalf("stage=%s want BODY", resp.Simd.Fault.Stage)
	}
	if !resp.Diff.FaultsEqual {
		t.Fatalf("fault classification mismatch: %+v", resp.Diff.FaultSummary)
	}
	// Prefix before fault committed; later lanes untouched.
	want := []int64{10, 7, 0, 0, 0}
	assertSlice(t, "out-prefix", resp.Simd.Arrays[0], want)
}

func assertSlice(t *testing.T, name string, got, want []int64) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("%s len=%d want %d (%v)", name, len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%s[%d]=%d want %d (full=%v)", name, i, got[i], want[i], got)
		}
	}
}
