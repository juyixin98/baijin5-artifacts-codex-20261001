// Package pipeline wires frontend -> IR -> linker -> runtime for the CLI.
package pipeline

import (
	"fmt"
	"os"
	"path/filepath"

	"mylnk/internal/frontend"
	"mylnk/internal/ir"
	"mylnk/internal/linker"
	"mylnk/internal/objfmt"
	"mylnk/internal/runtime"
)

// AssembleFile assembles one .asm file and returns the object.
func AssembleFile(path string) (*objfmt.Object, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	name := filepath.Base(path)
	if filepath.Ext(name) == ".asm" {
		name = name[:len(name)-len(".asm")] + ".o"
	}
	return frontend.Assemble(f, name)
}

// LoadMkobj reads an assembled object file.
func LoadMkobj(path string) (*objfmt.Object, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return objfmt.Decode(b)
}

// LinkedImage bundles IR analysis outputs for callers.
type LinkedImage struct {
	Program *ir.Program
	Reach   *ir.ReachResult
	Image   *linker.Image
}

// LinkObjects performs the full static pipeline and returns diagnostics.
func LinkObjects(objs []*objfmt.Object, entry string) (*LinkedImage, []*ir.LinkError, error) {
	p, err := ir.Build(objs, entry)
	if err != nil {
		return nil, nil, err
	}
	rr := p.AnalyzeReach()
	if errs := p.UndefinedDiagnostics(rr); len(errs) != 0 {
		return &LinkedImage{Program: p, Reach: rr}, errs, nil
	}
	img, err := linker.Link(p, rr)
	if err != nil {
		return &LinkedImage{Program: p, Reach: rr}, nil, err
	}
	return &LinkedImage{Program: p, Reach: rr, Image: img}, nil, nil
}

// Run executes a linked image.
func Run(li *LinkedImage, maxSteps int) (*runtime.Result, error) {
	if li == nil || li.Image == nil {
		return nil, fmt.Errorf("pipeline: no linked image")
	}
	return runtime.Run(li.Image, maxSteps, false)
}
