// Package diag builds structured, request-correlated diagnostics and performs
// semantic differential checks between the scalar reference and the masked
// SIMD execution.
package diag

import (
	"fmt"

	"simdc/internal/sem"
	"simdc/internal/transform"
)

// Event is one traceable diagnostic entry.
type Event struct {
	RequestID string                 `json:"request_id"`
	Stage     string                 `json:"stage"`
	Verdict   string                 `json:"verdict,omitempty"`
	Message   string                 `json:"message"`
	KeyState  map[string]interface{} `json:"key_state,omitempty"`
	Issues    []transform.Issue      `json:"issues,omitempty"`
}

// Diff captures semantic-equivalence evidence per output channel.
type Diff struct {
	Match        bool              `json:"match"`
	FaultsEqual  bool              `json:"faults_equal"`
	Mismatches   []ChannelMismatch `json:"mismatches,omitempty"`
	FaultSummary *FaultSummary     `json:"fault_summary,omitempty"`
}

// ChannelMismatch describes one differing scalar output element.
type ChannelMismatch struct {
	Output    string `json:"output"`
	Index     int    `json:"index"`
	Reference int64  `json:"reference"`
	Simd      int64  `json:"simd"`
}

// FaultSummary records fault classification agreement.
type FaultSummary struct {
	RefCode   string `json:"reference_code,omitempty"`
	SimdCode  string `json:"simd_code,omitempty"`
	RefIndex  int    `json:"reference_index"`
	SimdIndex int    `json:"simd_index"`
	RefStage  string `json:"reference_stage,omitempty"`
	SimdStage string `json:"simd_stage,omitempty"`
}

// Compare diffs two execution results element-by-element and compares the
// first fault's category and location.
func Compare(ref, simd *sem.Result) Diff {
	d := Diff{Match: true, FaultsEqual: true}
	for name, ra := range ref.Outputs.Arrays {
		sa, ok := simd.Outputs.Arrays[name]
		if !ok {
			d.Match = false
			d.Mismatches = append(d.Mismatches, missingArray(name)...)
			continue
		}
		for i := range ra {
			if i >= len(sa) {
				d.Match = false
				d.Mismatches = append(d.Mismatches, ChannelMismatch{Output: name, Index: i, Reference: ra[i]})
				continue
			}
			if ra[i] != sa[i] {
				d.Match = false
				d.Mismatches = append(d.Mismatches, ChannelMismatch{
					Output: name, Index: i, Reference: ra[i], Simd: sa[i],
				})
			}
		}
	}
	for name, rv := range ref.Outputs.Scalars {
		sv, ok := simd.Outputs.Scalars[name]
		if !ok || rv != sv {
			d.Match = false
			d.Mismatches = append(d.Mismatches, ChannelMismatch{
				Output: name, Index: -1, Reference: rv, Simd: sv,
			})
		}
	}
	fs := &FaultSummary{}
	var rc, sc string
	var ri, si int
	var rst, sst string
	if ref.Fault != nil {
		rc = string(ref.Fault.Code)
		ri = ref.Fault.GlobalIdx
		rst = string(ref.Fault.Stage)
	}
	if simd.Fault != nil {
		sc = string(simd.Fault.Code)
		si = simd.Fault.GlobalIdx
		sst = string(simd.Fault.Stage)
	}
	fs.RefCode, fs.SimdCode = rc, sc
	fs.RefIndex, fs.SimdIndex = ri, si
	fs.RefStage, fs.SimdStage = rst, sst
	d.FaultSummary = fs
	if (ref.Fault == nil) != (simd.Fault == nil) || rc != sc || ri != si || rst != sst {
		d.FaultsEqual = false
		d.Match = false
	}
	return d
}

func missingArray(name string) []ChannelMismatch {
	return []ChannelMismatch{{Output: fmt.Sprintf("%s(missing)", name), Index: -1}}
}

// Redact replaces array payloads with a length-only descriptor before logging
// so sensitive data is never printed.
func RedactArrays(arrays map[string][]int64) map[string]int {
	out := map[string]int{}
	for k, v := range arrays {
		out[k] = len(v)
	}
	return out
}
