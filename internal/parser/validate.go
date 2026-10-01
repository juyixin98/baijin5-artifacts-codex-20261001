package parser

import (
	"fmt"

	"scopelang/internal/ast"
	"scopelang/internal/diag"
)

type frame struct {
	names map[string]bool
	loop  bool
}

// Validate runs frontend semantic checks. All failures here are InputError:
// a well-formed program must never trip these.
func Validate(root *ast.Root) error {
	if root == nil || root.Body == nil {
		return inputErr("VALIDATE", "empty program")
	}
	f := &frame{names: map[string]bool{}}
	return checkBlock(root.Body, f)
}

func checkBlock(b *ast.Block, f *frame) error {
	if b == nil {
		return inputErr("VALIDATE", "missing block")
	}
	for _, s := range b.Stmts {
		switch n := s.(type) {
		case *ast.Acquire:
			if n.Name == "" {
				return inputErr("VALIDATE", "acquire without a name")
			}
			if f.names[n.Name] {
				return inputErrAt(n.Line, n.Start.Column,
					fmt.Sprintf("resource %q is already acquired in this scope", n.Name))
			}
			f.names[n.Name] = true
			if err := checkCleanup(n); err != nil {
				return err
			}
		case *ast.Block:
			inner := &frame{names: map[string]bool{}, loop: f.loop}
			if err := checkBlock(n, inner); err != nil {
				return err
			}
		case *ast.Repeat:
			if n.Count < 0 {
				return inputErrAt(n.Line, n.Pos.Column, "repeat count must be non-negative")
			}
			inner := &frame{names: map[string]bool{}, loop: true}
			if err := checkBlock(n.Body, inner); err != nil {
				return err
			}
		case *ast.Break:
			if !f.loop {
				return inputErrAt(n.Line, n.Pos.Column, "break outside of repeat loop")
			}
		case *ast.Emit, *ast.Fail, *ast.Return:
			// no context restrictions beyond the parser grammar
		default:
			return inputErr("VALIDATE", fmt.Sprintf("unsupported statement %T", s))
		}
	}
	return nil
}

func checkCleanup(a *ast.Acquire) error {
	fails := 0
	for _, s := range a.Cleanup.Stmts {
		switch n := s.(type) {
		case *ast.Emit:
			// allowed
		case *ast.Fail:
			fails++
			if fails > 1 {
				return inputErrAt(n.Line, n.Pos.Column, "onexit block may contain at most one fail")
			}
		default:
			return inputErrAt(a.Line, a.Start.Column,
				"onexit block may only contain emit and fail")
		}
	}
	return nil
}

func inputErr(code, msg string) *diag.Error {
	return diag.New(diag.Input, code, msg).With(diag.PhaseFrontend, "", 0)
}

func inputErrAt(line, col int, msg string) *diag.Error {
	return diag.New(diag.Input, "VALIDATE", fmt.Sprintf("%d:%d: %s", line, col, msg)).
		With(diag.PhaseFrontend, "", 0)
}
