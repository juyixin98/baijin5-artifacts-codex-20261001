package frontend

import "testing"

const smokeSource = `
input a[6]int64, b[6]int64, d[6]int64, idx[6]int64, t[6]int64
output out[6]int64, sum int64
for i := 0 .. len(a) {
  if (t[i] != 0) {
    out[i] = a[i] / d[i] + b[idx[i]]
  }
}
for j := 0 .. len(a) { reduce sum += a[j] }
`

func TestParseAndCheckSmoke(t *testing.T) {
	prog, err := Parse(smokeSource)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	info, err := Check(prog)
	if err != nil {
		t.Fatalf("check: %v", err)
	}
	if got := info.Lengths["a"]; got != 6 {
		t.Fatalf("length a = %d, want 6", got)
	}
	if len(prog.Reduces) != 1 || prog.Reduces[0].Op != "+" {
		t.Fatalf("reduce not parsed: %+v", prog.Reduces)
	}
}

func TestRejectUnknownTarget(t *testing.T) {
	src := `input a[2]int64
output out[2]int64
for i := 0 .. len(a) { ghost[i] = a[i] }`
	if _, err := Parse(src); err != nil {
		t.Fatalf("parse: %v", err)
	}
	prog, perr := Parse(src)
	if perr != nil {
		t.Fatalf("parse: %v", perr)
	}
	if _, err := Check(prog); err == nil {
		t.Fatal("expected checker rejection for undeclared output")
	}
}
