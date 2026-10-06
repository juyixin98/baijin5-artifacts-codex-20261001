package service

import (
	"encoding/json"
	"net/http"

	"pmd/diff"
	"pmd/runtime"
)

type compileRequest struct {
	Source string `json:"source"`
}

func (s *Server) compile(w http.ResponseWriter, r *http.Request) {
	reqID := requestID(r)
	var req compileRequest
	if !s.decode(w, r, reqID, &req) {
		return
	}
	prog, aerr := s.parseProgram(reqID, req.Source)
	if aerr != nil {
		s.fail(w, reqID, http.StatusBadRequest, aerr)
		return
	}
	tr := s.compileTree(reqID, prog)
	s.writeEnv(w, reqID, http.StatusOK, envelope{
		OK: true,
		Result: map[string]any{
			"tree":  tr,
			"stats": tr.Stats,
		},
		Warnings: tr.Warnings,
	})
}

type matchRequest struct {
	Source string          `json:"source"`
	Value  json.RawMessage `json:"value"`
	Verify *bool           `json:"verify,omitempty"`
}

func (s *Server) match(w http.ResponseWriter, r *http.Request) {
	reqID := requestID(r)
	var req matchRequest
	if !s.decode(w, r, reqID, &req) {
		return
	}
	prog, aerr := s.parseProgram(reqID, req.Source)
	if aerr != nil {
		s.fail(w, reqID, http.StatusBadRequest, aerr)
		return
	}
	tr := s.compileTree(reqID, prog)
	if len(req.Value) == 0 {
		s.fail(w, reqID, http.StatusBadRequest, &apiError{Category: CatBadRequest, Message: "value is required"})
		return
	}
	var val runtime.Value
	if err := json.Unmarshal(req.Value, &val); err != nil {
		s.fail(w, reqID, http.StatusBadRequest, &apiError{Category: CatBadRequest, Message: "invalid value JSON: " + err.Error()})
		return
	}
	outcome := runtime.EvalTree(tr, &val)
	result := map[string]any{
		"outcome":    outcome,
		"tree_stats": tr.Stats,
	}
	verify := req.Verify == nil || *req.Verify
	if verify {
		seq := runtime.EvalSequential(prog, &val)
		agree, reason := diff.CompareOutcomes(outcome, seq)
		result["verification"] = map[string]any{
			"reference_engine": "sequential",
			"agree":            agree,
			"reason":           reason,
		}
	}
	failureCat := ""
	if outcome.Failure != nil {
		failureCat = outcome.Failure.Category
	}
	s.log.Info("match evaluated",
		"request_id", reqID,
		"module", "runtime",
		"module_version", runtime.Version,
		"engine", "tree",
		"matched", outcome.Matched,
		"branch", outcome.Branch,
		"failure", failureCat,
		"effects", len(outcome.Effects),
	)
	s.writeEnv(w, reqID, http.StatusOK, envelope{
		OK:       true,
		Result:   result,
		Warnings: tr.Warnings,
	})
}

type diffRequest struct {
	Source   string `json:"source"`
	Cases    int    `json:"cases,omitempty"`
	Seed     int64  `json:"seed,omitempty"`
	MaxDepth int    `json:"max_depth,omitempty"`
}

func (s *Server) diff(w http.ResponseWriter, r *http.Request) {
	reqID := requestID(r)
	var req diffRequest
	if !s.decode(w, r, reqID, &req) {
		return
	}
	prog, aerr := s.parseProgram(reqID, req.Source)
	if aerr != nil {
		s.fail(w, reqID, http.StatusBadRequest, aerr)
		return
	}
	cases := req.Cases
	if cases <= 0 {
		cases = s.cfg.DiffDefaultCases
	}
	depth := req.MaxDepth
	if depth <= 0 {
		depth = s.cfg.DiffMaxDepth
	}
	values := diff.Enumerate(prog.Ctors, prog.CtorOrder, s.cfg.EnumDepth, s.cfg.EnumLimit)
	values = append(values, diff.Random(prog.Ctors, prog.CtorOrder, req.Seed, cases, depth)...)
	rep := diff.Run(prog, values)
	s.log.Info("diff completed",
		"request_id", reqID,
		"module", "diff",
		"module_version", diff.Version,
		"total", rep.Total,
		"compared", rep.Compared,
		"equal", rep.Equal,
		"mismatches", len(rep.Mismatches),
		"uncertain", len(rep.Uncertain),
	)
	s.writeEnv(w, reqID, http.StatusOK, envelope{
		OK:        true,
		Result:    rep,
		Uncertain: rep.Uncertain,
	})
}
