package transform

import (
	"fmt"

	"simdc/internal/frontend"
	"simdc/internal/ir"
)

// Options controls lowering.
type Options struct {
	Width   int
	Scalars map[string]int64 // request-provided scalar inputs for bound solving
}

// Lower parses, checks, and lowers source into a masked IR program.
// Scalars are needed only to resolve loop bounds at request time.
func Lower(source string, opts Options) Result {
	if opts.Width <= 0 {
		return Result{Verdict: VerdictReject, Issues: []Issue{{
			Code: CodeBadWidth, Message: fmt.Sprintf("simd width must be positive, got %d", opts.Width),
		}}}
	}
	prog, err := frontend.Parse(source)
	if err != nil {
		return Result{Verdict: VerdictReject, Issues: issuesFromErr(CodeSyntax, err)}
	}
	info, err := frontend.Check(prog)
	if err != nil {
		return Result{Verdict: VerdictReject, Issues: issuesFromErr(CodeSemantic, err)}
	}
	if len(prog.Body) != 1 {
		return Result{Verdict: VerdictReject, Issues: []Issue{{
			Code:    CodeUnsupportedShape,
			Message: fmt.Sprintf("exactly one counted loop supported, got %d", len(prog.Body)),
		}}}
	}
	loop := prog.Body[0].(*frontend.ForStmt)
	scalars := opts.Scalars
	if scalars == nil {
		scalars = map[string]int64{}
	}
	begin, beginOK, beginCode := ResolveBound(loop.Begin, info.Lengths, scalars)
	end, endOK, endCode := ResolveBound(loop.End, info.Lengths, scalars)
	if !beginOK {
		return Result{Verdict: VerdictUndetermined, Issues: []Issue{{
			Code: beginCode, Message: "loop begin bound cannot be resolved from the request",
		}}}
	}
	if !endOK {
		return Result{Verdict: VerdictUndetermined, Issues: []Issue{{
			Code: endCode, Message: "loop end bound cannot be resolved from the request",
		}}}
	}
	if begin != 0 {
		return Result{Verdict: VerdictReject, Issues: RejectShape("only loops beginning at 0 are supported")}
	}
	endName := boundArrayName(loop.End)
	if endName != "" && end > info.Lengths[endName] {
		return Result{Verdict: VerdictReject, Issues: []Issue{{
			Code:    CodeUnsupportedShape,
			Message: fmt.Sprintf("loop bound %d exceeds length of %s (%d)", end, endName, info.Lengths[endName]),
		}}}
	}
	if end < 0 {
		return Result{Verdict: VerdictReject, Issues: RejectShape("loop upper bound must be non-negative")}
	}

	l := newLower(opts.Width, info, prog)
	l.indexVar = loop.Var
	root := l.newMask()
	l.body = append(l.body, ir.Instr{
		Op: ir.OpMaskTail, MDst: root, Imm: end, StmtID: -1,
	})
	ordered := stmtOrder(loop.Body)
	if err := l.lowerStmts(loop.Body, root, ordered); err != nil {
		return Result{Verdict: VerdictReject, Issues: []Issue{{
			Code: CodeUnsupportedShape, Message: err.Error(),
		}}}
	}
	var reds []ir.ReduceInstr
	for _, r := range prog.Reduces {
		reds = append(reds, ir.ReduceInstr{Rop: r.Op, Target: r.Target, Source: r.Source})
	}
	irp := &ir.Program{
		Width:          opts.Width,
		IndexVar:       loop.Var,
		BeginConst:     begin,
		EndName:        endName,
		Body:           l.body,
		Reduces:        reds,
		NumRegs:        l.regs,
		NumMasks:       l.masks,
		ReductionOrder: "LEFT_FOLD_ASCENDING_INDEX",
	}
	if err := irp.Validate(); err != nil {
		return Result{Verdict: VerdictReject, Issues: []Issue{{
			Code: CodeIRUnsafe, Message: err.Error(),
		}}}
	}
	return Result{Verdict: VerdictAccept, Program: irp}
}

func RejectShape(msg string) []Issue {
	return []Issue{{Code: CodeUnsupportedShape, Message: msg}}
}

// boundArrayName returns the array name when the expression is len(name).
func boundArrayName(e frontend.Expr) string {
	if le, ok := e.(*frontend.LenExpr); ok {
		return le.Name
	}
	return ""
}
