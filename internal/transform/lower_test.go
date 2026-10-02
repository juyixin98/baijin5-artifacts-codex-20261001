package transform

import "testing"

const lowerSrc = `
input a[6]int64, d[6]int64, idx[6]int64, t[6]int64
output out[6]int64, sum int64
for i := 0 .. len(a) {
  if (t[i] != 0) {
    out[i] = a[i] / d[i]
  }
}
for j := 0 .. len(a) { reduce sum += a[j] }
`

func TestLowerAcceptsAndMasksTraps(t *testing.T) {
	res := Lower(lowerSrc, Options{Width: 4})
	if res.Verdict != VerdictAccept {
		t.Fatalf("verdict=%s issues=%+v", res.Verdict, res.Issues)
	}
	var gathers, divs, stores int
	for _, in := range res.Program.Body {
		switch in.Op {
		case "VLOAD":
			gathers++
			if in.Mask == 0 {
				t.Fatal("gather without mask")
			}
		case "VBIN":
			if in.Bop == "/" {
				divs++
				if in.Mask == 0 {
					t.Fatal("division without predicate")
				}
			}
		case "VSTORE":
			stores++
			if in.Mask == 0 {
				t.Fatal("store without mask")
			}
		}
	}
	if gathers != 3 || divs != 1 || stores != 1 {
		t.Fatalf("gathers=%d divs=%d stores=%d", gathers, divs, stores)
	}
	if got := res.Program.ReductionOrder; got != "LEFT_FOLD_ASCENDING_INDEX" {
		t.Fatalf("reduction order = %q", got)
	}
}

func TestLowerUndeterminedBound(t *testing.T) {
	src := `input a[3]int64, n int64
output out[3]int64
for i := 0 .. n { out[i] = a[i] }`
	res := Lower(src, Options{Width: 4})
	if res.Verdict != VerdictUndetermined {
		t.Fatalf("verdict=%s, want UNDETERMINED", res.Verdict)
	}
	// providing the scalar resolves it
	res2 := Lower(src, Options{Width: 4, Scalars: map[string]int64{"n": 3}})
	if res2.Verdict != VerdictAccept {
		t.Fatalf("with scalar, verdict=%s issues=%+v", res2.Verdict, res2.Issues)
	}
}
