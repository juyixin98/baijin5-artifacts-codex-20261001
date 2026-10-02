package tests

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"simdc/internal/pipeline"
)

type genFixture struct {
	Name    string `json:"name"`
	Request struct {
		RequestID string             `json:"request_id"`
		Source    string             `json:"source"`
		Width     int                `json:"simd_width"`
		Scalars   map[string]int64   `json:"scalars"`
		Arrays    map[string][]int64 `json:"arrays"`
	} `json:"request"`
	Expected struct {
		Verdict    string             `json:"verdict"`
		Halted     bool               `json:"halted"`
		FaultCode  string             `json:"fault_code"`
		FaultIndex int                `json:"fault_index"`
		FaultStage string             `json:"fault_stage"`
		OutOrder   []string           `json:"out_order"`
		OutArrays  map[string][]int64 `json:"out_arrays"`
		OutScalars map[string]int64   `json:"out_scalars"`
	} `json:"expected"`
}

func TestGeneratedFixtures(t *testing.T) {
	matches, err := filepath.Glob(filepath.Join("..", "testdata", "generated", "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(matches) == 0 {
		t.Fatal("no generated fixtures; run: go run ./cmd/genfixtures")
	}
	for _, path := range matches {
		path := path
		name := strings.TrimSuffix(filepath.Base(path), ".json")
		t.Run(name, func(t *testing.T) {
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var fx genFixture
			if err := json.Unmarshal(raw, &fx); err != nil {
				t.Fatal(err)
			}
			req := pipeline.Request{
				RequestID: fx.Request.RequestID,
				Source:    fx.Request.Source,
				Width:     fx.Request.Width,
				Scalars:   fx.Request.Scalars,
				Arrays:    fx.Request.Arrays,
			}
			resp := pipeline.Execute(req)
			if string(resp.Verdict) != fx.Expected.Verdict {
				t.Fatalf("verdict=%s want %s issues=%+v", resp.Verdict, fx.Expected.Verdict, resp.Issues)
			}
			if resp.Simd == nil {
				t.Fatal("missing SIMD result")
			}
			if resp.Diff == nil || !resp.Diff.Match {
				t.Fatalf("reference/SIMD differential mismatch: %+v", resp.Diff)
			}
			if resp.Simd.Halted != fx.Expected.Halted {
				t.Fatalf("halted=%v want %v", resp.Simd.Halted, fx.Expected.Halted)
			}
			if fx.Expected.FaultCode != "" {
				if resp.Simd.Fault == nil {
					t.Fatal("expected fault, got none")
				}
				if resp.Simd.Fault.Code != fx.Expected.FaultCode {
					t.Fatalf("fault code=%s want %s", resp.Simd.Fault.Code, fx.Expected.FaultCode)
				}
				if resp.Simd.Fault.GlobalIdx != fx.Expected.FaultIndex {
					t.Fatalf("fault idx=%d want %d", resp.Simd.Fault.GlobalIdx, fx.Expected.FaultIndex)
				}
				if resp.Simd.Fault.Stage != fx.Expected.FaultStage {
					t.Fatalf("fault stage=%s want %s", resp.Simd.Fault.Stage, fx.Expected.FaultStage)
				}
			}
			for i, outName := range fx.Expected.OutOrder {
				want := fx.Expected.OutArrays[outName]
				got := resp.Simd.Arrays[i]
				assertSlice(t, outName, got, want)
			}
			for name, want := range fx.Expected.OutScalars {
				got, ok := resp.Simd.Scalars[name]
				if !ok {
					t.Fatalf("missing scalar output %q", name)
				}
				if got != want {
					t.Fatalf("scalar %s=%d want %d", name, got, want)
				}
			}
		})
	}
}
