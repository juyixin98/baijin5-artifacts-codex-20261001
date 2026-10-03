package frontend_test

import (
	"strings"
	"testing"

	"rlmod/internal/frontend"
)

func TestParseMinimalProgram(t *testing.T) {
	src := "package demo\n\nconst Answer int = 6 * 7\n\nfn F(x: int): int {\n\treturn x + Answer\n}\n"
	p, err := frontend.Parse(src)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if p.Module != "demo" {
		t.Fatalf("module = %q", p.Module)
	}
	if len(p.Clauses) != 2 {
		t.Fatalf("clauses = %d", len(p.Clauses))
	}
	if p.Clauses[0].Const == nil || p.Clauses[0].Const.Name != "Answer" {
		t.Fatalf("first clause not const Answer: %+v", p.Clauses[0])
	}
	fn := p.Clauses[1].Func
	if fn == nil || fn.Name != "F" || fn.Result.Name != "int" || len(fn.Params) != 1 {
		t.Fatalf("bad func clause: %+v", fn)
	}
}

func TestParseGenericAndQualified(t *testing.T) {
	src := "package m\nfn Id[T](v: T): T { return v }\nfn Use(): int { return other::Id[int](3) }\n"
	p, err := frontend.Parse(src)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if got := p.Clauses[0].Func.TypeParams; len(got) != 1 || got[0] != "T" {
		t.Fatalf("type params = %v", got)
	}
	call := p.Clauses[1].Func.Body[0].Return.Value.Call
	if call == nil || call.Module != "other" || call.Name != "Id" {
		t.Fatalf("qualified call not parsed: %+v", call)
	}
	if len(call.TypeArgs) != 1 || call.TypeArgs[0].Name != "int" {
		t.Fatalf("type args = %+v", call.TypeArgs)
	}
}

func TestParseErrorsHavePositions(t *testing.T) {
	cases := map[string]string{
		"unterminated string": "package m\nconst X str = \"abc\n",
		"stray char":          "package m\nfn F() { @ }\n",
		"missing paren":       "package m\nfn F( { }\n",
	}
	for name, src := range cases {
		t.Run(name, func(t *testing.T) {
			_, err := frontend.Parse(src)
			if err == nil {
				t.Fatal("expected error")
			}
			if !strings.Contains(err.Error(), "line ") {
				t.Fatalf("error lacks position: %v", err)
			}
		})
	}
}
