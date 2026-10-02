// Package transform lowers checked scalar-condition loops into the
// restricted masked-SIMD ir.Program.
package transform

import (
	"fmt"

	"simdc/internal/frontend"
	"simdc/internal/ir"
)

// Verdict classifies a lowering outcome.
type Verdict string

const (
	VerdictAccept       Verdict = "ACCEPT"
	VerdictReject       Verdict = "REJECT"
	VerdictUndetermined Verdict = "UNDETERMINED"
)

// IssueCode is a stable diagnostic code.
type IssueCode string

const (
	CodeSyntax           IssueCode = "FRONTEND_SYNTAX"
	CodeSemantic         IssueCode = "FRONTEND_SEMANTIC"
	CodeUnsupportedShape IssueCode = "UNSUPPORTED_SHAPE"
	CodeBoundUnresolved  IssueCode = "BOUND_UNRESOLVED"
	CodeBadWidth         IssueCode = "BAD_SIMD_WIDTH"
	CodeIRUnsafe         IssueCode = "IR_MASK_UNSAFE"
)

// Issue is one structured reason.
type Issue struct {
	Code    IssueCode `json:"code"`
	Message string    `json:"message"`
	Pos     string    `json:"pos,omitempty"`
}

// Result carries either a lowered program or classified issues.
type Result struct {
	Verdict Verdict
	Program *ir.Program
	Issues  []Issue
}

// ResolveBound evaluates the counted-loop end bound against compile-time
// array lengths and optional scalar inputs. Supported forms:
//   - integer constant;
//   - len(arr);
//   - sums/differences of the above (e.g. len(a)-1).
//
// Unresolved bounds produce BOUND_UNRESOLVED rather than a guess.
func ResolveBound(e frontend.Expr, lengths map[string]int64, scalars map[string]int64) (int64, bool, IssueCode) {
	switch t := e.(type) {
	case *frontend.IntLit:
		return t.Value, true, ""
	case *frontend.LenExpr:
		if n, ok := lengths[t.Name]; ok {
			return n, true, ""
		}
		return 0, false, CodeBoundUnresolved
	case *frontend.Ident:
		if v, ok := scalars[t.Name]; ok {
			return v, true, ""
		}
		return 0, false, CodeBoundUnresolved
	case *frontend.BinaryExpr:
		l, okL, _ := ResolveBound(t.Left, lengths, scalars)
		r, okR, _ := ResolveBound(t.Right, lengths, scalars)
		if !okL || !okR {
			return 0, false, CodeBoundUnresolved
		}
		switch t.Op {
		case "+":
			return l + r, true, ""
		case "-":
			return l - r, true, ""
		case "*":
			return l * r, true, ""
		default:
			return 0, false, CodeBoundUnresolved
		}
	case *frontend.UnaryExpr:
		if t.Op == "-" {
			v, ok, _ := ResolveBound(t.Inner, lengths, scalars)
			if !ok {
				return 0, false, CodeBoundUnresolved
			}
			return -v, true, ""
		}
	}
	return 0, false, CodeBoundUnresolved
}

func issuesFromErr(code IssueCode, err error) []Issue {
	return []Issue{{Code: code, Message: err.Error()}}
}

var _ = fmt.Sprintf
