package runtime

import "scopelang/internal/diag"

// Status is the terminal outcome kind.
type Status string

const (
	StatusOK     Status = "ok"
	StatusReturn Status = "return"
	StatusFail   Status = "fail"
)

// Step is an interpreter/VM intermediate-state snapshot used by replay logs.
type Step struct {
	IP       int      `json:"ip,omitempty"`
	Op       string   `json:"op,omitempty"`
	Reason   string   `json:"reason,omitempty"`
	Line     int      `json:"line,omitempty"`
	Point    string   `json:"point,omitempty"`
	Instance int      `json:"instance,omitempty"`
	Note     string   `json:"note,omitempty"`
	Guards   []bool   `json:"guards,omitempty"`
	Live     []string `json:"live,omitempty"`
}

// Outcome is the full, comparable result of one run.
type Outcome struct {
	RunID     string       `json:"run_id"`
	Engine    string       `json:"engine"`
	Status    Status       `json:"status"`
	ReturnVal string       `json:"return_value,omitempty"`
	Error     *diag.Error  `json:"error,omitempty"`
	Events    []Event      `json:"events"`
	Steps     []Step       `json:"steps,omitempty"`
}

// Policy implements the fixed cleanup-error policy shared by both engines:
//
//  1. The first error of the whole run is the primary error and never changes.
//  2. Cleanup still runs to completion; later cleanup errors attach as
//     "suppressed" errors, preserving point and category.
//  3. A cleanup error during normal exit converts the run to fail; during
//     break it converts the unwind to fail (break never suppresses an error);
//     during return/fail it attaches while preserving the original reason.
type Policy struct {
	primary *diag.Error
}

// Observe records an error. reason is the unwind mode active when the error
// was raised; it determines the resulting reason after attachment.
func (p *Policy) Observe(err *diag.Error, reason string) string {
	if err == nil {
		return reason
	}
	if p.primary == nil {
		p.primary = err
		if reason == reasonNormal || reason == reasonBreak {
			return reasonFail
		}
		return reason
	}
	p.primary.Attach(err)
	if reason == reasonNormal || reason == reasonBreak {
		return reasonFail
	}
	return reason
}

func (p *Policy) Primary() *diag.Error { return p.primary }

const (
	reasonNormal = "normal"
	reasonBreak  = "break"
	reasonReturn = "return"
	reasonFail   = "fail"
)
