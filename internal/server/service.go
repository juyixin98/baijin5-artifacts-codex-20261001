// Package server exposes the restricted BER codec over a small, bounded
// HTTP API. Every request gets a run id; failures always carry an error
// category and the exact byte offset, and unknown/exceptional states are
// reported as errors rather than success.
package server

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"berconf/internal/ber"
	"berconf/internal/logx"
	"berconf/internal/store"
)

// CodecService is the protocol state machine around the byte codec:
// request -> size gate -> hex decode -> (traced) parse -> profile
// validation -> audit -> response. Each step has an explicit status.
type CodecService struct {
	Limits  ber.Limits
	Log     *logx.Logger
	Store   *store.Store
	Version string

	seq atomic.Int64
}

// Envelope is the single response shape for every endpoint.
type Envelope struct {
	Success bool          `json:"success"`
	Data    any           `json:"data,omitempty"`
	Error   *ErrorDetail  `json:"error,omitempty"`
	Meta    *ResponseMeta `json:"meta,omitempty"`
}

type ErrorDetail struct {
	Category string `json:"category"`
	Message  string `json:"message"`
	Offset   *int64 `json:"offset,omitempty"`
	Depth    int    `json:"depth,omitempty"`
}

type ResponseMeta struct {
	RunID         string `json:"run_id"`
	DurationUS    int64  `json:"duration_us"`
	Version       string `json:"version"`
	DecisionBasis string `json:"decision_basis,omitempty"`
}

// NodeDTO is the JSON representation of a decoded value, including exact
// byte offsets for correlation with the input.
type NodeDTO struct {
	Class       string    `json:"class"`
	Tag         uint32    `json:"tag"`
	Constructed bool      `json:"constructed"`
	Indefinite  bool      `json:"indefinite_length,omitempty"`
	ValueHex    string    `json:"value_hex,omitempty"`
	Start       int64     `json:"start_offset"`
	HeaderEnd   int64     `json:"header_end_offset,omitempty"`
	End         int64     `json:"end_offset"`
	Children    []NodeDTO `json:"children,omitempty"`
}

func nodeToDTO(n *ber.Node) NodeDTO {
	d := NodeDTO{
		Class:       n.Class.String(),
		Tag:         n.Tag,
		Constructed: n.Constructed,
		Indefinite:  n.Indefinite,
		Start:       n.Start,
		HeaderEnd:   n.HeaderEnd,
		End:         n.End,
	}
	if len(n.Value) > 0 {
		d.ValueHex = hex.EncodeToString(n.Value)
	}
	for _, c := range n.Children {
		d.Children = append(d.Children, nodeToDTO(c))
	}
	return d
}

type decodeRequest struct {
	InputHex string `json:"input_hex"`
	Mode     string `json:"mode"` // "" | BER | DER
	Trace    bool   `json:"trace"`
}

type encodeRequest struct {
	Node     nodeInput `json:"node"`
	Encoding string    `json:"encoding"` // BER | DER | BER_INDEFINITE
}

// nodeInput accepts a JSON value description from clients.
type nodeInput struct {
	Class       string      `json:"class"` // universal | context
	Tag         uint32      `json:"tag"`
	Constructed bool        `json:"constructed"`
	ValueHex    string      `json:"value_hex"`
	Children    []nodeInput `json:"children"`
}

type validateRequest struct {
	InputHex string `json:"input_hex"`
	Mode     string `json:"mode"` // BER | DER (required)
}

func (s *CodecService) write(w http.ResponseWriter, status int, env Envelope) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(env)
}

func (s *CodecService) fail(w http.ResponseWriter, status int, runID string, category, msg string, started time.Time, offset *int64, depth int) {
	s.write(w, status, Envelope{
		Success: false,
		Error:   &ErrorDetail{Category: category, Message: msg, Offset: offset, Depth: depth},
		Meta: &ResponseMeta{
			RunID:      runID,
			DurationUS: time.Since(started).Microseconds(),
			Version:    s.Version,
		},
	})
}

func (s *CodecService) newRunID() string { return logx.NewRunID() }

// HandleDecode implements POST /v1/decode.
func (s *CodecService) HandleDecode(w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	runID := s.newRunID()
	var req decodeRequest
	if !s.bind(w, r, runID, started, &req) {
		return
	}
	raw, ok := s.decodeHex(w, runID, started, req.InputHex)
	if !ok {
		return
	}
	lg := s.Log.With(logx.InputFingerprint(raw))
	var tracer ber.Tracer
	var stepMu sync.Mutex
	var steps int
	if req.Trace {
		tracer = lg.Tracer(runID, &steps, &stepMu)
	}
	mode := normMode(req.Mode)
	lg.Info("decode begin", map[string]any{
		"run_id": runID, "op": "decode", "mode": mode, "request_seq": s.seq.Add(1),
	})

	var node *ber.Node
	var derr error
	if mode == "DER" {
		node, derr = ber.DecodeAndValidate(raw, "DER", s.Limits)
	} else {
		node, derr = ber.DecodeTraced(raw, s.Limits, tracer)
		if derr == nil {
			derr = node.ValidateBER(s.Limits)
		}
	}

	if derr != nil {
		s.audit(runID, "decode", mode, raw, derr, started)
		s.emitDecodeError(w, runID, started, derr, lg)
		return
	}
	if err := s.Store.Insert(store.Record{
		RunID: runID, Op: "decode", Mode: mode, Status: "ok",
		InputLen: len(raw), InputSHA256: fingerprint(raw),
		OutputLen: int(node.End), DurationMicros: time.Since(started).Microseconds(),
	}); err != nil {
		lg.Error("audit insert failed", map[string]any{"run_id": runID, "err": err.Error()})
	}
	lg.Info("decode ok", map[string]any{"run_id": runID, "bytes": node.End})
	s.write(w, http.StatusOK, Envelope{
		Success: true,
		Data:    map[string]any{"node": nodeToDTO(node)},
		Meta: &ResponseMeta{
			RunID: runID, DurationUS: time.Since(started).Microseconds(),
			Version: s.Version, DecisionBasis: "X.690 BER structural decode + restricted-profile validation",
		},
	})
}

// HandleValidate implements POST /v1/validate.
func (s *CodecService) HandleValidate(w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	runID := s.newRunID()
	var req validateRequest
	if !s.bind(w, r, runID, started, &req) {
		return
	}
	if req.Mode != "BER" && req.Mode != "DER" {
		s.fail(w, http.StatusBadRequest, runID, "INVALID_REQUEST",
			"mode must be BER or DER", started, nil, 0)
		return
	}
	raw, ok := s.decodeHex(w, runID, started, req.InputHex)
	if !ok {
		return
	}
	node, derr := ber.DecodeAndValidate(raw, req.Mode, s.Limits)
	if derr != nil {
		s.audit(runID, "validate", req.Mode, raw, derr, started)
		s.emitDecodeError(w, runID, started, derr, s.Log.With(logx.InputFingerprint(raw)))
		return
	}
	if err := s.Store.Insert(store.Record{
		RunID: runID, Op: "validate", Mode: req.Mode, Status: "ok",
		InputLen: len(raw), InputSHA256: fingerprint(raw),
		OutputLen: int(node.End), DurationMicros: time.Since(started).Microseconds(),
	}); err != nil {
		s.Log.Error("audit insert failed", map[string]any{"run_id": runID, "err": err.Error()})
	}
	s.write(w, http.StatusOK, Envelope{
		Success: true,
		Data:    map[string]any{"valid": true, "mode": req.Mode, "node": nodeToDTO(node)},
		Meta: &ResponseMeta{RunID: runID, DurationUS: time.Since(started).Microseconds(), Version: s.Version,
			DecisionBasis: basisForMode(req.Mode)},
	})
}

// HandleEncode implements POST /v1/encode.
func (s *CodecService) HandleEncode(w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	runID := s.newRunID()
	var req encodeRequest
	if !s.bind(w, r, runID, started, &req) {
		return
	}
	enc, err := encodingFromName(req.Encoding)
	if err != nil {
		s.fail(w, http.StatusBadRequest, runID, "INVALID_REQUEST", err.Error(), started, nil, 0)
		return
	}
	node, err := nodeFromInput(req.Node)
	if err != nil {
		s.fail(w, http.StatusBadRequest, runID, "INVALID_REQUEST", err.Error(), started, nil, 0)
		return
	}
	out, encErr := ber.EncodeChecked(node, enc)
	if encErr != nil {
		_ = s.Store.Insert(store.Record{
			RunID: runID, Op: "encode", Mode: req.Encoding, Status: "error",
			ErrorKind: string(ber.KindEncodeError), ErrorOffset: -1,
			InputLen: 0, InputSHA256: "", DurationMicros: time.Since(started).Microseconds(),
		})
		s.fail(w, http.StatusUnprocessableEntity, runID, string(ber.KindEncodeError),
			encErr.Error(), started, nil, 0)
		return
	}
	// Encoder output must itself satisfy the requested profile.
	checkMode := "BER"
	if enc == ber.DER {
		checkMode = "DER"
	}
	if _, verr := ber.DecodeAndValidate(out, checkMode, s.Limits); verr != nil {
		s.Log.Error("encoder produced invalid output", map[string]any{
			"run_id": runID, "err": verr.Error(),
		})
		s.fail(w, http.StatusInternalServerError, runID, "INTERNAL_INCONSISTENCY",
			"encoded output failed self-validation: "+verr.Error(), started, nil, 0)
		return
	}
	_ = s.Store.Insert(store.Record{
		RunID: runID, Op: "encode", Mode: req.Encoding, Status: "ok",
		InputLen: 0, InputSHA256: "", OutputLen: len(out),
		DurationMicros: time.Since(started).Microseconds(),
	})
	s.write(w, http.StatusOK, Envelope{
		Success: true,
		Data:    map[string]any{"output_hex": hex.EncodeToString(out), "output_len": len(out)},
		Meta: &ResponseMeta{RunID: runID, DurationUS: time.Since(started).Microseconds(), Version: s.Version,
			DecisionBasis: basisForMode(checkMode)},
	})
}

// HandleHealth reports liveness; it never reports ok for a broken store.
func (s *CodecService) HandleHealth(w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	runID := s.newRunID()
	if _, err := s.Store.Recent(1); err != nil {
		s.fail(w, http.StatusServiceUnavailable, runID, "UNHEALTHY",
			"audit store unavailable: "+err.Error(), started, nil, 0)
		return
	}
	s.write(w, http.StatusOK, Envelope{
		Success: true,
		Data:    map[string]any{"status": "ok"},
		Meta:    &ResponseMeta{RunID: runID, Version: s.Version, DurationUS: time.Since(started).Microseconds()},
	})
}

func (s *CodecService) bind(w http.ResponseWriter, r *http.Request, runID string, started time.Time, dst any) bool {
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		s.fail(w, http.StatusBadRequest, runID, "INVALID_REQUEST",
			"request body must be JSON: "+err.Error(), started, nil, 0)
		return false
	}
	return true
}

func (s *CodecService) decodeHex(w http.ResponseWriter, runID string, started time.Time, hx string) ([]byte, bool) {
	raw, err := hex.DecodeString(hx)
	if err != nil {
		s.fail(w, http.StatusBadRequest, runID, "INVALID_HEX",
			"input_hex is not valid even-length hexadecimal: "+err.Error(), started, nil, 0)
		return nil, false
	}
	return raw, true
}

func (s *CodecService) emitDecodeError(w http.ResponseWriter, runID string, started time.Time, derr error, lg interface {
	Info(string, map[string]any)
}) {
	var de *ber.DecodeError
	if errors.As(derr, &de) {
		off := de.Offset
		lg.Info("decode failed", map[string]any{
			"run_id": runID, "kind": string(de.Kind), "offset": de.Offset, "depth": de.Depth,
		})
		s.fail(w, statusForKind(de.Kind), runID, string(de.Kind), de.Msg, started, &off, de.Depth)
		return
	}
	s.fail(w, http.StatusInternalServerError, runID, "INTERNAL", derr.Error(), started, nil, 0)
}

func (s *CodecService) audit(runID, op, mode string, raw []byte, derr error, started time.Time) {
	var de *ber.DecodeError
	kind, off := "", int64(-1)
	if errors.As(derr, &de) {
		kind, off = string(de.Kind), de.Offset
	}
	if err := s.Store.Insert(store.Record{
		RunID: runID, Op: op, Mode: mode, Status: "error",
		ErrorKind: kind, ErrorOffset: off,
		InputLen: len(raw), InputSHA256: fingerprint(raw), OutputLen: 0,
		DurationMicros: time.Since(started).Microseconds(),
	}); err != nil {
		s.Log.Error("audit insert failed", map[string]any{"run_id": runID, "err": err.Error()})
	}
}

func statusForKind(k ber.ErrorKind) int {
	switch k {
	case ber.KindSizeExceeded, ber.KindDepthExceeded, ber.KindLengthOverflow:
		return http.StatusRequestEntityTooLarge // 413: resource bound rejected
	case ber.KindUnsupported:
		return http.StatusUnprocessableEntity // 422: well-formed but out of profile
	default:
		return http.StatusBadRequest // 400: malformed encoding
	}
}

func normMode(m string) string {
	if m == "DER" {
		return "DER"
	}
	return "BER"
}

func basisForMode(m string) string {
	if m == "DER" {
		return "X.690 clause 9-11: DER definite-length and minimal canonical form"
	}
	return "X.690 clause 8: BER, restricted to INTEGER/BIT STRING/SEQUENCE/SET/context tags"
}

func encodingFromName(name string) (ber.Encoding, error) {
	switch name {
	case "", "BER":
		return ber.BER, nil
	case "DER":
		return ber.DER, nil
	case "BER_INDEFINITE", "INDEFINITE":
		return ber.BERIndefinite, nil
	default:
		return ber.BER, fmt.Errorf("encoding must be BER, DER or BER_INDEFINITE, got %q", name)
	}
}

func classFromName(name string) (ber.Class, error) {
	switch name {
	case "", "universal":
		return ber.ClassUniversal, nil
	case "context":
		return ber.ClassContext, nil
	default:
		// application/private tags are outside the restricted profile
		return 0, fmt.Errorf("class must be universal or context, got %q", name)
	}
}

func nodeFromInput(in nodeInput) (*ber.Node, error) {
	cls, err := classFromName(in.Class)
	if err != nil {
		return nil, err
	}
	n := &ber.Node{Class: cls, Tag: in.Tag, Constructed: in.Constructed}
	if in.Constructed {
		for i, c := range in.Children {
			cn, err := nodeFromInput(c)
			if err != nil {
				return nil, fmt.Errorf("children[%d]: %w", i, err)
			}
			n.Children = append(n.Children, cn)
		}
	} else {
		v, err := hex.DecodeString(in.ValueHex)
		if err != nil {
			return nil, fmt.Errorf("value_hex: %w", err)
		}
		n.Value = v
	}
	return n, nil
}

func fingerprint(raw []byte) string {
	fp := logx.InputFingerprint(raw)
	if s, ok := fp["input_sha256"].(string); ok {
		return s
	}
	return ""
}
