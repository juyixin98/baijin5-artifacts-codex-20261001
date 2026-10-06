// Package frontend implements the language front end for the pattern-match
// compiler: lexing, parsing and semantic validation of match programs.
package frontend

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strconv"
)

// Version is the module version reported by the service layer.
const Version = "0.1.0"

// Error categories produced by this module.
const (
	CatParse    = "parse_error"
	CatSemantic = "semantic_error"
)

// Pos is a 1-based source position.
type Pos struct {
	Line int `json:"line"`
	Col  int `json:"col"`
}

func (p Pos) String() string { return fmt.Sprintf("%d:%d", p.Line, p.Col) }

// Error is a categorized front-end error with an optional source position.
type Error struct {
	Category string `json:"category"`
	Message  string `json:"message"`
	Pos      *Pos   `json:"position,omitempty"`
}

func (e *Error) Error() string {
	if e.Pos != nil {
		return fmt.Sprintf("%s at %s: %s", e.Category, e.Pos, e.Message)
	}
	return fmt.Sprintf("%s: %s", e.Category, e.Message)
}

func errorf(cat string, pos Pos, format string, args ...any) *Error {
	p := pos
	return &Error{Category: cat, Message: fmt.Sprintf(format, args...), Pos: &p}
}

// LitKind discriminates literal values.
type LitKind string

const (
	LitInt  LitKind = "int"
	LitStr  LitKind = "string"
	LitBool LitKind = "bool"
)

// LitValue is a literal constant. It is comparable with ==.
type LitValue struct {
	Kind LitKind
	Int  int64
	Str  string
	Bool bool
}

func IntLit(v int64) LitValue  { return LitValue{Kind: LitInt, Int: v} }
func StrLit(s string) LitValue { return LitValue{Kind: LitStr, Str: s} }
func BoolLit(b bool) LitValue  { return LitValue{Kind: LitBool, Bool: b} }

func (l LitValue) String() string {
	switch l.Kind {
	case LitInt:
		return strconv.FormatInt(l.Int, 10)
	case LitStr:
		return strconv.Quote(l.Str)
	case LitBool:
		return strconv.FormatBool(l.Bool)
	}
	return "<bad-literal>"
}

// MarshalJSON renders the literal as a plain JSON value (1, "a", true).
func (l LitValue) MarshalJSON() ([]byte, error) {
	switch l.Kind {
	case LitInt, LitBool:
		return []byte(l.String()), nil
	case LitStr:
		return json.Marshal(l.Str)
	}
	return nil, fmt.Errorf("invalid literal kind %q", l.Kind)
}

// UnmarshalJSON parses a plain JSON value into a literal.
func (l *LitValue) UnmarshalJSON(data []byte) error {
	var v any
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		return fmt.Errorf("invalid literal JSON: %w", err)
	}
	switch t := v.(type) {
	case json.Number:
		n, err := strconv.ParseInt(t.String(), 10, 64)
		if err != nil {
			return fmt.Errorf("number literal %q is not an integer", t.String())
		}
		*l = IntLit(n)
	case string:
		*l = StrLit(t)
	case bool:
		*l = BoolLit(t)
	default:
		return fmt.Errorf("unsupported literal JSON %s", string(data))
	}
	return nil
}

// Patterns.

type Pattern interface{ patNode() }

// PWildcard is the "_" pattern.
type PWildcard struct{ P Pos }

// PVar binds a variable.
type PVar struct {
	Name string
	P    Pos
}

// PLit matches a literal constant.
type PLit struct {
	Val LitValue
	P   Pos
}

// PCtor matches a constructor application.
type PCtor struct {
	Name string
	Args []Pattern
	P    Pos
}

func (PWildcard) patNode() {}
func (PVar) patNode()      {}
func (PLit) patNode()      {}
func (PCtor) patNode()     {}

// Guard expressions.

type Expr interface{ exprNode() }

type ELit struct {
	Val LitValue
	P   Pos
}

type EVar struct {
	Name string
	P    Pos
}

type ECall struct {
	Func string
	Args []Expr
	P    Pos
}

type EAnd struct {
	L, R Expr
	P    Pos
}

type EOr struct {
	L, R Expr
	P    Pos
}

type ENot struct {
	E Expr
	P Pos
}

func (ELit) exprNode()  {}
func (EVar) exprNode()  {}
func (ECall) exprNode() {}
func (EAnd) exprNode()  {}
func (EOr) exprNode()   {}
func (ENot) exprNode()  {}

// Builtins lists the guard functions with their arities. "and", "or" and
// "not" are syntax, not functions. effect(label, cond) evaluates cond,
// records a side effect and yields cond.
var Builtins = map[string]int{
	"eq": 2, "ne": 2,
	"lt": 2, "le": 2, "gt": 2, "ge": 2,
	"even": 1, "odd": 1, "pos": 1, "neg": 1,
	"effect": 2,
}

// Branch is one "| pattern [if guard] => label" alternative.
type Branch struct {
	Index int
	Pat   Pattern
	Guard Expr // nil when absent
	Label string
	P     Pos
}

// Program is a validated match expression plus its constructor table.
type Program struct {
	Ctors     map[string]int
	CtorOrder []string
	Scrutinee string
	Branches  []Branch
	Source    string
}
