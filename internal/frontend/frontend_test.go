package frontend

import "testing"

func TestParseAndLexErrors(t *testing.T) {
	lexErrors := []string{
		"pub fn f(x: int) -> int { return x @ 1; }",
		"pub const S: string = \"abc;\n",
	}
	for i, src := range lexErrors {
		if _, err := Lex(src); err == nil {
			t.Fatalf("lex case %d: expected lexer error", i)
		}
		if _, err := Parse("t.rl", src); err == nil {
			t.Fatalf("lex case %d: Parse must propagate lexer error", i)
		}
	}
	parseErrors := []string{
		"pub fn f(x: int -> int { return x; }",
		"pub fn f(x: 123) -> int { return 1; }",
		"pub fn f( -> int { }",
		"pub fn f() int { }",
		"pub const K int = 1;",
		"module ;\npub fn f() -> int { return 1; }",
		"pub fn f() -> int { let = 1; return 2; }",
	}
	for i, src := range parseErrors {
		if _, err := Parse("t.rl", src); err == nil {
			t.Fatalf("parse case %d: expected parser error for %q", i, src)
		}
	}
}

func TestParseValid(t *testing.T) {
	srcs := []string{
		"module m;\npub fn f() -> int { return 1; }\n",
		"module m;\nimport n;\npub fn g() -> int { return n::f(1) + n::K; }\n",
		"module m;\npub generic fn id(x: T) -> T { return x; }\n",
		"module m;\npub fn f(x: int) -> int { if (x < 2) { return 1; } else { return 2; } }\n",
	}
	for i, src := range srcs {
		m, err := Parse("t.rl", src)
		if err != nil {
			t.Fatalf("valid case %d: %v", i, err)
		}
		if m.Name != "m" {
			t.Fatalf("case %d: module name %s", i, m.Name)
		}
	}
}
