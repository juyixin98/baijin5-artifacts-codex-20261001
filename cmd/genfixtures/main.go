// Command genfixtures regenerates the committed local synthetic test
// fixtures. It never imports the SIMD runtime: expected outputs are produced
// by an independent, deliberately explicit hand-coded scalar computation
// embedded in this generator. The Go test suite then runs the full pipeline
// against these fixtures and asserts every element.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
)

type Fixture struct {
	Name     string             `json:"name"`
	Request  FixtureRequest     `json:"request"`
	Expected FixtureExpectation `json:"expected"`
}

type FixtureRequest struct {
	RequestID string             `json:"request_id"`
	Source    string             `json:"source"`
	Width     int                `json:"simd_width"`
	Scalars   map[string]int64   `json:"scalars"`
	Arrays    map[string][]int64 `json:"arrays"`
}

type FixtureExpectation struct {
	Verdict    string             `json:"verdict"`
	Halted     bool               `json:"halted"`
	FaultCode  string             `json:"fault_code,omitempty"`
	FaultIndex int                `json:"fault_index,omitempty"`
	FaultStage string             `json:"fault_stage,omitempty"`
	OutOrder   []string           `json:"out_order"`
	OutArrays  map[string][]int64 `json:"out_arrays"`
	OutScalars map[string]int64   `json:"out_scalars"`
}

const divSource = `input a[5]int64, d[5]int64, t[5]int64
output out[5]int64
for i := 0 .. len(a) {
  if (t[i] != 0) {
    out[i] = a[i] / d[i]
  }
}
`

const gatherSource = `input a[6]int64, b[4]int64, idx[6]int64, t[6]int64
output out[6]int64
for i := 0 .. len(a) {
  if (t[i] != 0) {
    out[i] = b[idx[i]] + a[i]
  }
}
`

const reduceSource = `input a[5]int64
output sum int64, prod int64, digits int64, touched[5]int64
for i := 0 .. len(a) {
  touched[i] = a[i]
}
for j := 0 .. len(a) { reduce sum += a[j] }
for k := 0 .. len(a) { reduce prod *= a[k] }
for m := 0 .. len(a) { reduce digits concat= a[m] }
`

// expectedDiv is the explicitly tabulated oracle for the div fixture.
func expectedDiv(a, d, t []int64) ([]int64, string, int) {
	out := make([]int64, 5)
	for i := 0; i < 5; i++ {
		if t[i] == 0 {
			continue // masked: no division, output stays zero
		}
		if d[i] == 0 {
			return out, "DIV_BY_ZERO", i
		}
		out[i] = a[i] / d[i]
	}
	return out, "", -1
}

// expectedGather tabulates gathers independently, with explicit bounds.
func expectedGather(a, b, idx, t []int64) ([]int64, string, int) {
	out := make([]int64, 6)
	for i := 0; i < 6; i++ {
		if t[i] == 0 {
			continue
		}
		j := idx[i]
		if j < 0 || j >= int64(len(b)) {
			return out, "INDEX_OUT_OF_BOUNDS", i
		}
		out[i] = b[j] + a[i]
	}
	return out, "", -1
}

func main() {
	dir := flag.String("out", "testdata/generated", "output directory")
	flag.Parse()
	if err := os.MkdirAll(*dir, 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fixtures := build()
	for _, fx := range fixtures {
		path := filepath.Join(*dir, fx.Name+".json")
		b, err := json.MarshalIndent(fx, "", "  ")
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if err := os.WriteFile(path, append(b, '\n'), 0o644); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Println("wrote", path)
	}
}

func build() []Fixture {
	divA := []int64{20, 7, 9, 16, 10}
	divD := []int64{2, 0, 3, 4, 5}
	divT := []int64{1, 0, 1, 1, 1}
	divOut, divCode, divIdx := expectedDiv(divA, divD, divT)

	gA := []int64{1, 2, 3, 4, 5, 6}
	gB := []int64{10, 20, 30, 40}
	gIdx := []int64{0, 99, 1, -7, 3, 2}
	gT := []int64{1, 0, 1, 0, 1, 1}
	gOut, gCode, gIdx2 := expectedGather(gA, gB, gIdx, gT)

	redA := []int64{1, 2, 3, 4, 5}

	return []Fixture{
		{
			Name: "masked_div_tail",
			Request: FixtureRequest{
				RequestID: "fx-div-001", Source: divSource, Width: 4,
				Scalars: map[string]int64{},
				Arrays:  map[string][]int64{"a": divA, "d": divD, "t": divT},
			},
			Expected: FixtureExpectation{
				Verdict: "ACCEPT", Halted: divCode != "",
				FaultCode: divCode, FaultIndex: divIdx, FaultStage: "BODY",
				OutOrder:  []string{"out"},
				OutArrays: map[string][]int64{"out": divOut},
			},
		},
		{
			Name: "masked_gather_oob",
			Request: FixtureRequest{
				RequestID: "fx-gather-002", Source: gatherSource, Width: 4,
				Scalars: map[string]int64{},
				Arrays:  map[string][]int64{"a": gA, "b": gB, "idx": gIdx, "t": gT},
			},
			Expected: FixtureExpectation{
				Verdict: "ACCEPT", Halted: gCode != "",
				FaultCode: gCode, FaultIndex: gIdx2, FaultStage: "BODY",
				OutOrder:  []string{"out"},
				OutArrays: map[string][]int64{"out": gOut},
			},
		},
		{
			Name: "reduce_order",
			Request: FixtureRequest{
				RequestID: "fx-reduce-003", Source: reduceSource, Width: 3,
				Scalars: map[string]int64{},
				Arrays:  map[string][]int64{"a": redA},
			},
			Expected: FixtureExpectation{
				Verdict:   "ACCEPT",
				OutOrder:  []string{"touched"},
				OutArrays: map[string][]int64{"touched": redA},
				OutScalars: map[string]int64{
					"sum": 15, "prod": 120, "digits": 12345,
				},
			},
		},
	}
}
