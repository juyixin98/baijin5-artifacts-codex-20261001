package syntax

import "testing"

const factSrc = `
pure fn fact(n) {
  if (n < 2) {
    return 1
  } else {
    return n * fact(n - 1)
  }
}
`

func TestParseFactorialShape(t *testing.T) {
	prog, err := Parse(factSrc)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if len(prog.Funcs) != 1 {
		t.Fatalf("funcs = %d, want 1", len(prog.Funcs))
	}
	fn := prog.Funcs[0]
	if fn.Name != "fact" || !fn.Pure {
		t.Fatalf("decl = %+v, want pure fact", fn)
	}
	if len(fn.Params) != 1 || fn.Params[0] != "n" {
		t.Fatalf("params = %v, want [n]", fn.Params)
	}
	ifs, ok := fn.Body[0].(*IfStmt)
	if !ok {
		t.Fatalf("body[0] = %T, want *IfStmt", fn.Body[0])
	}
	if ifs.Else == nil {
		t.Fatal("else branch missing")
	}
}

func TestParseImplicitAndExplicitSemicolons(t *testing.T) {
	src := `fn f(a) { let x = 1 + 2; return x; }
fn g(a) {
  let x = a
  return x
}`
	prog, err := Parse(src)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if len(prog.Funcs) != 2 {
		t.Fatalf("funcs = %d, want 2", len(prog.Funcs))
	}
	if got := len(prog.Funcs[1].Body); got != 2 {
		t.Fatalf("g body = %d stmts, want 2", got)
	}
}

func TestParseHexLiteral(t *testing.T) {
	prog, err := Parse(`pure fn f() { return 0x10 }`)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	ret := prog.Funcs[0].Body[0].(*ReturnStmt)
	lit := ret.Value.(*IntLit)
	if lit.Value != 16 {
		t.Fatalf("hex = %d, want 16", lit.Value)
	}
}
