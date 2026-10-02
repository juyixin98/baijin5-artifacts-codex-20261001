package specializer

import (
	"errors"
	"fmt"

	"funcspec/internal/interp"
	"funcspec/internal/ir"
)

// errFoldBudget indicates the compile-time fold budget ran out; the caller
// residualizes instead of folding.
var errFoldBudget = errors.New("compile fold budget exhausted")

// foldDepth bounds recursion while evaluating pure calls at compile time.
const foldDepth = 100_000

// foldPure executes a pure user function with all-known arguments using the
// same interpreter kernel that runs residual programs. Purity was validated
// during lowering, so no side effect can be observed, and the total number of
// compiled calls is bounded by cfg.FoldBudget.
func (st *state) foldPure(name string, args []ir.Value) (ir.Value, error) {
	if st.folds >= st.cfg.FoldBudget {
		return ir.Value{}, errFoldBudget
	}
	aborted := false
	opts := interp.Options{
		MaxDepth: foldDepth,
		OnCall: func(string) {
			st.stat.CallsFolded++
			st.folds++
			if st.folds >= st.cfg.FoldBudget {
				aborted = true
			}
		},
	}
	res, err := interp.Run(st.src, name, args, opts)
	if err != nil {
		return ir.Value{}, fmt.Errorf("fold %s aborted: %w", name, err)
	}
	if aborted {
		return ir.Value{}, errFoldBudget
	}
	return ir.Int(res.Value), nil
}
