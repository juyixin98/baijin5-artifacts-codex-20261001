package specializer_test

import (
	"io"
	"testing"

	"funcspec/internal/config"
	"funcspec/internal/ir"
	"funcspec/internal/observ"
	"funcspec/internal/specializer"
	"funcspec/internal/syntax"
)

func TestDbg2(t *testing.T) {
	src := `
pure fn scale(base, x) { return base * x + 1 }
fn driver(n) {
  if (n <= 0) { return 0 } else {
    emit(scale(3, n))
    return driver(n - 1)
  }
}
`
	prog, _ := syntax.Parse(src)
	irp, _ := ir.Lower(prog)
	rid, iid := observ.NewRun("driver", []byte(src), nil)
	res, err := specializer.Specialize(irp, "driver",
		[]specializer.Arg{{Static: false}}, config.Default(), observ.New(io.Discard, rid, iid))
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("order=%v entry=%s", res.Residual.Order, res.Entry)
	for name, fn := range res.Residual.Funcs {
		t.Logf("FUNC %s params=%v", name, fn.Params)
		for _, s := range fn.Body {
			t.Logf("  %#v", s)
		}
	}
}
