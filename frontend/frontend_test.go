package frontend

import (
	"strings"
	"testing"
)

const listSrc = `
ctor Nil 0
ctor Cons 2

match xs {
  | Cons(x, Cons(y, Nil)) if effect("pair-check", eq(x, y)) => "pair-eq"
  | Cons(x, Cons(_, _)) => "long"
  | Cons(x, Nil) if even(x) => "single-even"
  | Cons(_, Nil) => "single"
  | Nil => "empty"
}
`

func TestParseListProgram(t *testing.T) {
	prog, err := Parse(listSrc)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if len(prog.Ctors) != 2 || prog.Ctors["Nil"] != 0 || prog.Ctors["Cons"] != 2 {
		t.Errorf("ctor table wrong: %v", prog.Ctors)
	}
	if prog.Scrutinee != "xs" {
		t.Errorf("scrutinee = %q", prog.Scrutinee)
	}
	if len(prog.Branches) != 5 {
		t.Fatalf("branches = %d, want 5", len(prog.Branches))
	}
	b0 := prog.Branches[0]
	call, ok := b0.Guard.(ECall)
	if !ok || call.Func != "effect" || len(call.Args) != 2 {
		t.Fatalf("branch 0 guard = %#v", b0.Guard)
	}
	ctor, ok := b0.Pat.(PCtor)
	if !ok || ctor.Name != "Cons" || len(ctor.Args) != 2 {
		t.Fatalf("branch 0 pattern = %#v", b0.Pat)
	}
	if _, ok := ctor.Args[0].(PVar); !ok {
		t.Errorf("branch 0 arg 0 should be a variable, got %#v", ctor.Args[0])
	}
	p4, ok := prog.Branches[4].Pat.(PCtor)
	if !ok || p4.Name != "Nil" || len(p4.Args) != 0 {
		t.Errorf("branch 4 pattern = %#v", prog.Branches[4].Pat)
	}
	if prog.Branches[4].Label != "empty" {
		t.Errorf("branch 4 label = %q", prog.Branches[4].Label)
	}
}

func TestParseErrors(t *testing.T) {
	cases := []struct {
		name string
		src  string
		cat  string
		sub  string
	}{
		{"unbound-var-in-guard", "ctor A 1\nmatch v {\n | A(x) if gt(z, 1) => \"b\"\n}", CatSemantic, `unbound variable "z"`},
		{"duplicate-var", "ctor P 2\nmatch v {\n | P(x, x) => \"b\"\n}", CatSemantic, `duplicate variable "x"`},
		{"unknown-ctor", "match v {\n | Foo(x) => \"b\"\n}", CatSemantic, `unknown constructor "Foo"`},
		{"arity-mismatch", "ctor P 2\nmatch v {\n | P(x) => \"b\"\n}", CatSemantic, "expects 2 argument(s), got 1"},
		{"unknown-guard-fn", "ctor A 1\nmatch v {\n | A(x) if frobnicate(x) => \"b\"\n}", CatSemantic, `unknown guard function "frobnicate"`},
		{"guard-fn-arity", "ctor A 1\nmatch v {\n | A(x) if eq(x) => \"b\"\n}", CatSemantic, "expects 2 argument(s), got 1"},
		{"missing-arrow", "ctor A 0\nmatch v {\n | A = \"b\"\n}", CatParse, "did you mean '=>'"},
		{"bad-char", "ctor A 0\nmatch v {\n | A @ \"b\"\n}", CatParse, "unexpected character"},
		{"lowercase-ctor-decl", "ctor a 0\nmatch v {\n | _ => \"b\"\n}", CatSemantic, "must start with an uppercase"},
		{"duplicate-ctor-decl", "ctor A 0\nctor A 0\nmatch v {\n | _ => \"b\"\n}", CatSemantic, `duplicate constructor "A"`},
		{"empty-match", "match v {\n}", CatParse, "at least one branch"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := Parse(tc.src)
			if err == nil {
				t.Fatalf("expected error for %s", tc.src)
			}
			fe, ok := err.(*Error)
			if !ok {
				t.Fatalf("expected *Error, got %T (%v)", err, err)
			}
			if fe.Category != tc.cat {
				t.Errorf("category = %q, want %q (err=%v)", fe.Category, tc.cat, fe)
			}
			if !strings.Contains(fe.Message, tc.sub) {
				t.Errorf("message %q does not contain %q", fe.Message, tc.sub)
			}
			if fe.Pos == nil {
				t.Errorf("error has no position: %v", fe)
			}
		})
	}
}

func TestExprStringRoundTrip(t *testing.T) {
	prog, err := Parse("ctor A 1\nmatch v {\n | A(x) if not even(x) or gt(x, 3) and lt(x, 10) => \"b\"\n}")
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	got := ExprString(prog.Branches[0].Guard)
	want := "(not even(x) or (gt(x, 3) and lt(x, 10)))"
	if got != want {
		t.Errorf("ExprString = %q, want %q", got, want)
	}
}
