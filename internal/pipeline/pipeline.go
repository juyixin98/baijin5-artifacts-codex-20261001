// Package pipeline wires frontend -> transform -> runtime/reference ->
// differential diagnostics into one request-level entry point.
package pipeline

import (
	"fmt"

	"simdc/internal/diag"
	"simdc/internal/frontend"
	"simdc/internal/reference"
	"simdc/internal/runtime"
	"simdc/internal/sem"
	"simdc/internal/transform"
)

// Request is the full local synthetic invocation.
type Request struct {
	RequestID string             `json:"request_id"`
	Source    string             `json:"source"`
	Width     int                `json:"simd_width"`
	Scalars   map[string]int64   `json:"scalars"`
	Arrays    map[string][]int64 `json:"arrays"`
}

// Response is the verdict, IR summary, both execution results, and the diff.
type Response struct {
	RequestID string            `json:"request_id"`
	Verdict   transform.Verdict `json:"verdict"`
	Width     int               `json:"simd_width"`
	TripCount int64             `json:"trip_count,omitempty"`
	IRSummary string            `json:"ir_summary,omitempty"`
	Issues    []transform.Issue `json:"issues,omitempty"`
	Reference *ReferenceView    `json:"reference,omitempty"`
	Simd      *SimdView         `json:"simd,omitempty"`
	Diff      *diag.Diff        `json:"diff,omitempty"`
	Events    []diag.Event      `json:"events"`
}

// ReferenceView exposes concrete expected values (oracle-generated, not by
// the SIMD core).
type ReferenceView struct {
	Halted  bool             `json:"halted"`
	Fault   *FaultView       `json:"fault,omitempty"`
	Arrays  [][]int64        `json:"arrays,omitempty"`
	Scalars map[string]int64 `json:"scalars,omitempty"`
}

type SimdView struct {
	Halted  bool             `json:"halted"`
	Fault   *FaultView       `json:"fault,omitempty"`
	Arrays  [][]int64        `json:"arrays,omitempty"`
	Scalars map[string]int64 `json:"scalars,omitempty"`
}

type FaultView struct {
	Code      string `json:"code"`
	GlobalIdx int    `json:"global_idx"`
	StmtID    int    `json:"stmt_id"`
	Stage     string `json:"stage"`
	Detail    string `json:"detail,omitempty"`
}

// Execute runs the full conversion-and-check pipeline.
func Execute(req Request) Response {
	if req.Width == 0 {
		req.Width = 4
	}
	res := transform.Lower(req.Source, transform.Options{
		Width:   req.Width,
		Scalars: req.Scalars,
	})
	resp := Response{RequestID: req.RequestID, Verdict: res.Verdict, Width: req.Width, Issues: res.Issues}
	resp.Events = append(resp.Events, diag.Event{
		RequestID: req.RequestID,
		Stage:     "lower",
		Verdict:   string(res.Verdict),
		Message:   verdictMessage(res),
		KeyState: map[string]interface{}{
			"simd_width":  req.Width,
			"array_lens":  diag.RedactArrays(req.Arrays),
			"scalar_keys": scalarKeys(req.Scalars),
		},
		Issues: res.Issues,
	})
	if res.Verdict != transform.VerdictAccept {
		return resp
	}
	prog := res.Program
	resp.IRSummary = prog.String()

	parsed, _ := frontend.Parse(req.Source)
	info, err := frontend.Check(parsed)
	if err != nil {
		resp.Verdict = transform.VerdictReject
		resp.Issues = []transform.Issue{{Code: transform.CodeSemantic, Message: err.Error()}}
		return resp
	}
	lengths := mergeLengths(info, req)
	tripCount, code := resolveTrip(parsed, info, req.Scalars)
	if code != "" {
		resp.Verdict = transform.VerdictUndetermined
		resp.Issues = []transform.Issue{{Code: code, Message: "cannot resolve trip count for execution"}}
		return resp
	}
	resp.TripCount = tripCount
	if issue := validateInputs(parsed, req); issue != nil {
		resp.Verdict = transform.VerdictUndetermined
		resp.Issues = []transform.Issue{*issue}
		resp.Events = append(resp.Events, diag.Event{
			RequestID: req.RequestID, Stage: "validate_inputs",
			Verdict: "UNDETERMINED", Message: issue.Message,
			KeyState: map[string]interface{}{"array_lens": diag.RedactArrays(req.Arrays)},
		})
		return resp
	}

	outArrayLens := map[string]int{}
	var outScalars []string
	for _, d := range parsed.Outputs {
		if d.Kind == frontend.KindArray {
			outArrayLens[d.Name] = int(lengths[d.Name])
		} else {
			outScalars = append(outScalars, d.Name)
		}
	}
	d := reference.Data{
		Scalars: req.Scalars, Arrays: req.Arrays,
		OutputArrayLen: outArrayLens, OutputScalars: outScalars,
		TripCount: tripCount,
	}
	refRes, err := reference.Run(parsed, info, d)
	if err != nil {
		resp.Verdict = transform.VerdictUndetermined
		resp.Issues = []transform.Issue{{Code: "REFERENCE_ERROR", Message: err.Error()}}
		return resp
	}
	simdRes, err := runtime.Run(prog, runtime.Inputs{
		Scalars: req.Scalars, Arrays: req.Arrays,
		OutputArrayLen: outArrayLens, OutputScalars: outScalars,
		IterCount: tripCount,
	})
	if err != nil {
		resp.Verdict = transform.VerdictUndetermined
		resp.Issues = []transform.Issue{{Code: "SIMD_RUNTIME_ERROR", Message: err.Error()}}
		return resp
	}
	diff := diag.Compare(refRes, simdRes)
	resp.Reference = viewReference(refRes, parsed.Outputs)
	resp.Simd = viewSimd(simdRes, parsed.Outputs)
	resp.Diff = &diff
	resp.Events = append(resp.Events, diag.Event{
		RequestID: req.RequestID,
		Stage:     "differential",
		Verdict:   boolVerdict(diff.Match),
		Message:   diffMessage(diff),
		KeyState: map[string]interface{}{
			"trip_count":     tripCount,
			"faults_equal":   diff.FaultsEqual,
			"fault_summary":  diff.FaultSummary,
			"mismatch_count": len(diff.Mismatches),
		},
	})
	return resp
}

func validateInputs(prog *frontend.Program, req Request) *transform.Issue {
	for _, d := range prog.Inputs {
		switch d.Kind {
		case frontend.KindScalar:
			if _, ok := req.Scalars[d.Name]; !ok {
				return &transform.Issue{
					Code:    transform.IssueCode("MISSING_INPUT"),
					Message: fmt.Sprintf("request does not provide scalar input %q", d.Name),
				}
			}
		case frontend.KindArray:
			got, ok := req.Arrays[d.Name]
			if !ok {
				return &transform.Issue{
					Code:    transform.IssueCode("MISSING_INPUT"),
					Message: fmt.Sprintf("request does not provide array input %q", d.Name),
				}
			}
			want := int(progLength(d))
			if len(got) != want {
				return &transform.Issue{
					Code: transform.IssueCode("INPUT_LENGTH_MISMATCH"),
					Message: fmt.Sprintf("array %q has %d elements, declaration requires %d",
						d.Name, len(got), want),
				}
			}
		}
	}
	return nil
}

func progLength(d *frontend.Decl) int64 {
	v, err := frontend.EvalConstExported(d.Length)
	if err != nil {
		return -1
	}
	return v
}

func verdictMessage(r transform.Result) string {
	switch r.Verdict {
	case transform.VerdictAccept:
		return "lowered to masked SIMD IR; all traps carry predicates derived from tail/condition masks"
	case transform.VerdictReject:
		return "rejected: program violates language or mask-safety rules"
	default:
		return "undetermined: request does not supply enough information to decide"
	}
}

func boolVerdict(match bool) string {
	if match {
		return "MATCH"
	}
	return "MISMATCH"
}

func diffMessage(d diag.Diff) string {
	if d.Match {
		return "per-channel outputs and fault category/location agree with the scalar reference"
	}
	return fmt.Sprintf("differential mismatch: %d channel(s), faults_equal=%v", len(d.Mismatches), d.FaultsEqual)
}

func scalarKeys(m map[string]int64) []string {
	var keys []string
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

func mergeLengths(info *frontend.Info, req Request) map[string]int64 {
	out := map[string]int64{}
	for k, v := range info.Lengths {
		out[k] = v
	}
	return out
}

func resolveTrip(prog *frontend.Program, info *frontend.Info, scalars map[string]int64) (int64, transform.IssueCode) {
	loop := prog.Body[0].(*frontend.ForStmt)
	v, ok, code := transform.ResolveBound(loop.End, info.Lengths, scalars)
	if !ok {
		return 0, code
	}
	return v, ""
}

func viewReference(r *sem.Result, decls []*frontend.Decl) *ReferenceView {
	v := &ReferenceView{Halted: r.Halted, Scalars: map[string]int64{}}
	if r.Fault != nil {
		v.Fault = faultView(r.Fault)
	}
	v.Arrays = orderedArrays(r.Outputs.Arrays, decls)
	for _, d := range decls {
		if d.Kind == frontend.KindScalar {
			v.Scalars[d.Name] = r.Outputs.Scalars[d.Name]
		}
	}
	return v
}

func viewSimd(r *sem.Result, decls []*frontend.Decl) *SimdView {
	v := &SimdView{Halted: r.Halted, Scalars: map[string]int64{}}
	if r.Fault != nil {
		v.Fault = faultView(r.Fault)
	}
	v.Arrays = orderedArrays(r.Outputs.Arrays, decls)
	for _, d := range decls {
		if d.Kind == frontend.KindScalar {
			v.Scalars[d.Name] = r.Outputs.Scalars[d.Name]
		}
	}
	return v
}

func faultView(f *sem.Fault) *FaultView {
	return &FaultView{
		Code: string(f.Code), GlobalIdx: f.GlobalIdx, StmtID: f.StmtID,
		Stage: string(f.Stage), Detail: f.Detail,
	}
}

func orderedArrays(values map[string][]int64, decls []*frontend.Decl) [][]int64 {
	var out [][]int64
	for _, d := range decls {
		if d.Kind == frontend.KindArray {
			out = append(out, values[d.Name])
		}
	}
	return out
}
