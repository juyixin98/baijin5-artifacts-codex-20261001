package tests

import (
	"testing"

	"simdc/internal/pipeline"
)

func TestRejectSyntaxAndSemantic(t *testing.T) {
	cases := []struct {
		name   string
		source string
		code   string
	}{
		{
			name:   "syntax",
			source: "input a[2]int64\noutput o[2]int64\nfor i := 0 .. { }\n",
			code:   "FRONTEND_SYNTAX",
		},
		{
			name:   "assign_non_output",
			source: "input a[2]int64\noutput o[2]int64\nfor i := 0 .. len(a) { ghost[i] = a[i] }\n",
			code:   "FRONTEND_SEMANTIC",
		},
		{
			name: "nested_loop",
			source: "input a[2]int64\noutput o[2]int64\n" +
				"for i := 0 .. len(a) { for k := 0 .. 1 { o[i] = a[i] } }\n",
			code: "FRONTEND_SYNTAX",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp := pipeline.Execute(pipeline.Request{
				RequestID: "req-reject-" + tc.name,
				Source:    tc.source,
				Width:     4,
			})
			if string(resp.Verdict) != "REJECT" {
				t.Fatalf("verdict=%s want REJECT", resp.Verdict)
			}
			if len(resp.Issues) == 0 || string(resp.Issues[0].Code) != tc.code {
				t.Fatalf("issues=%+v want code %s", resp.Issues, tc.code)
			}
		})
	}
}

func TestUndeterminedBoundThenResolved(t *testing.T) {
	source := "input a[3]int64, n int64\noutput o[3]int64\nfor i := 0 .. n { o[i] = a[i] }\n"
	resp := pipeline.Execute(pipeline.Request{
		RequestID: "req-undet-030", Source: source, Width: 2,
	})
	if string(resp.Verdict) != "UNDETERMINED" || len(resp.Issues) == 0 ||
		string(resp.Issues[0].Code) != "BOUND_UNRESOLVED" {
		t.Fatalf("want BOUND_UNRESOLVED, got %s %+v", resp.Verdict, resp.Issues)
	}
	resp2 := pipeline.Execute(pipeline.Request{
		RequestID: "req-undet-030", Source: source, Width: 2,
		Scalars: map[string]int64{"n": 3},
		Arrays:  map[string][]int64{"a": {4, 5, 6}},
	})
	if string(resp2.Verdict) != "ACCEPT" || !resp2.Diff.Match {
		t.Fatalf("resolved request: verdict=%s diff=%+v", resp2.Verdict, resp2.Diff)
	}
	assertSlice(t, "o", resp2.Simd.Arrays[0], []int64{4, 5, 6})
}

func TestBadWidthRejected(t *testing.T) {
	source := "input a[2]int64\noutput o[2]int64\nfor i := 0 .. len(a) { o[i] = a[i] }\n"
	resp := pipeline.Execute(pipeline.Request{
		RequestID: "req-width-031", Source: source, Width: 0,
		Arrays: map[string][]int64{"a": {1, 2}},
	})
	// Width 0 is defaulted to 4; explicitly negative must reject.
	neg := pipeline.Execute(pipeline.Request{
		RequestID: "req-width-031b", Source: source, Width: -1,
	})
	_ = resp
	if string(neg.Verdict) != "REJECT" || string(neg.Issues[0].Code) != "BAD_SIMD_WIDTH" {
		t.Fatalf("negative width verdict=%s issues=%+v", neg.Verdict, neg.Issues)
	}
}
