package runtime

import (
	"os"

	"scopelang/internal/ast"
	"scopelang/internal/ir"
	"scopelang/internal/lower"
	"scopelang/internal/parser"
	"scopelang/internal/verify"
)

// PipelineResult carries the parsed/lowered artifacts so callers (CLI, diff
// harness, tests) can inspect each stage and attach stage-specific errors.
type PipelineResult struct {
	Root *ast.Root
	Prog *ir.Program
}

// FrontendAndLower parses source and lowers it to verified IR.
func FrontendAndLower(src string) (*PipelineResult, error) {
	root, err := parser.Parse(src)
	if err != nil {
		return nil, err
	}
	prog, err := lower.Compile(root)
	if err != nil {
		return nil, err
	}
	if err := verify.Program(prog); err != nil {
		return nil, err
	}
	return &PipelineResult{Root: root, Prog: prog}, nil
}

// RunBoth executes the same spec/source on the reference tree interpreter and
// the lowered VM. Both get independent hosts (fresh state), but identical
// configuration, which is the precondition for semantic comparison.
func RunBoth(src string, spec *RunSpec) (*Outcome, *Outcome, error) {
	pr, err := FrontendAndLower(src)
	if err != nil {
		return nil, nil, err
	}
	tree := RunTree(pr.Root, spec)
	vm := RunVM(pr.Prog, spec)
	return tree, vm, nil
}

// LoadProgramSource reads a .scope file; file read errors are InputError.
func LoadProgramSource(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", loadErr(path, err)
	}
	return string(data), nil
}
