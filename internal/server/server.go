// Package server exposes the BER codec as a controlled HTTP service with
// per-request audit logging.
package server

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"runtime"
	"time"

	"berd/internal/ber"
	"berd/internal/config"
	"berd/internal/store"
	"berd/internal/version"
)

// Server is the BER codec HTTP service.
type Server struct {
	cfg   config.Config
	st    *store.Store
	runID string
	log   *slog.Logger
	mux   *http.ServeMux
}

// New builds a Server. st may be nil to disable audit persistence.
func New(cfg config.Config, st *store.Store, logger *slog.Logger) *Server {
	if logger == nil {
		logger = slog.Default()
	}
	s := &Server{
		cfg:   cfg,
		st:    st,
		runID: newRunID(),
		log:   logger.With("component", "berd"),
	}
	s.mux = http.NewServeMux()
	s.mux.HandleFunc("GET /v1/health", s.handleHealth)
	s.mux.HandleFunc("GET /v1/config", s.handleConfig)
	s.mux.HandleFunc("POST /v1/decode", s.handleDecode)
	s.mux.HandleFunc("POST /v1/encode", s.handleEncode)
	s.mux.HandleFunc("POST /v1/canonicalize", s.handleCanonicalize)
	s.mux.HandleFunc("POST /v1/verify-der", s.handleVerifyDER)
	return s
}

// RunID identifies this server process in logs and audit rows.
func (s *Server) RunID() string { return s.runID }

// Handler returns the root HTTP handler.
func (s *Server) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		s.mux.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), startKey{}, start)))
	})
}

type startKey struct{}

func newRunID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("run-%d", time.Now().UnixNano())
	}
	return fmt.Sprintf("run-%x", b[:])
}

func newRequestID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("req-%d", time.Now().UnixNano())
	}
	return fmt.Sprintf("req-%x", b[:])
}

// envelope is the single response shape for every endpoint. Failures are
// never reported as success: ok=false always carries a classified error.
type envelope struct {
	OK     bool         `json:"ok"`
	Result any          `json:"result,omitempty"`
	Error  *envelopeErr `json:"error,omitempty"`
	RunID  string       `json:"run_id"`
	ReqID  string       `json:"request_id"`
}

type envelopeErr struct {
	Category ber.Category `json:"category"`
	Offset   int          `json:"offset"`
	Message  string       `json:"message"`
}

type codecRequest struct {
	Encoding string `json:"encoding"` // "hex" (default) or "base64"
	Data     string `json:"data"`
}

func (r *codecRequest) bytes() ([]byte, error) {
	switch r.Encoding {
	case "", "hex":
		return hex.DecodeString(r.Data)
	case "base64":
		return base64.StdEncoding.DecodeString(r.Data)
	}
	return nil, fmt.Errorf("unknown encoding %q", r.Encoding)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	s.writeOK(w, r, "health", nil, map[string]any{
		"status":     "up",
		"version":    version.Version,
		"go_version": runtime.Version(),
		"run_id":     s.runID,
	})
}

func (s *Server) handleConfig(w http.ResponseWriter, r *http.Request) {
	s.writeOK(w, r, "config", nil, map[string]any{
		"listen": s.cfg.Listen,
		"limits": s.cfg.Limits,
	})
}

func (s *Server) handleDecode(w http.ResponseWriter, r *http.Request) {
	var req codecRequest
	if !s.decodeBody(w, r, &req) {
		return
	}
	data, err := req.bytes()
	if err != nil {
		s.writeBadRequest(w, r, "decode", err.Error())
		return
	}
	root, perr := ber.DecodeAll(data, s.cfg.Limits)
	if perr != nil {
		s.writeCodecErr(w, r, "decode", data, perr)
		return
	}
	if verr := ber.Validate(root, s.cfg.Limits); verr != nil {
		s.writeCodecErr(w, r, "decode", data, verr)
		return
	}
	s.writeOK(w, r, "decode", data, map[string]any{
		"value": root.ToJSON(s.cfg.Limits),
	})
}

func (s *Server) handleEncode(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Form string   `json:"form"` // "ber" (default) or "der"
		Spec ber.Spec `json:"spec"`
	}
	if !s.decodeBody(w, r, &req) {
		return
	}
	node, berr := ber.Build(req.Spec, s.cfg.Limits)
	if berr != nil {
		s.writeCodecErr(w, r, "encode", nil, berr)
		return
	}
	var out []byte
	switch req.Form {
	case "", "ber":
		out = ber.EncodeBER(node)
	case "der":
		var derr *ber.Error
		out, derr = ber.EncodeDER(node, s.cfg.Limits)
		if derr != nil {
			s.writeCodecErr(w, r, "encode", nil, derr)
			return
		}
	default:
		s.writeBadRequest(w, r, "encode", fmt.Sprintf("unknown form %q", req.Form))
		return
	}
	s.writeOK(w, r, "encode", out, map[string]any{"hex": hex.EncodeToString(out)})
}

func (s *Server) handleCanonicalize(w http.ResponseWriter, r *http.Request) {
	var req codecRequest
	if !s.decodeBody(w, r, &req) {
		return
	}
	data, err := req.bytes()
	if err != nil {
		s.writeBadRequest(w, r, "canonicalize", err.Error())
		return
	}
	root, perr := ber.DecodeAll(data, s.cfg.Limits)
	if perr != nil {
		s.writeCodecErr(w, r, "canonicalize", data, perr)
		return
	}
	der, derr := ber.EncodeDER(root, s.cfg.Limits)
	if derr != nil {
		s.writeCodecErr(w, r, "canonicalize", data, derr)
		return
	}
	s.writeOK(w, r, "canonicalize", data, map[string]any{
		"der_hex": hex.EncodeToString(der),
		"changed": hex.EncodeToString(der) != hex.EncodeToString(data),
	})
}

func (s *Server) handleVerifyDER(w http.ResponseWriter, r *http.Request) {
	var req codecRequest
	if !s.decodeBody(w, r, &req) {
		return
	}
	data, err := req.bytes()
	if err != nil {
		s.writeBadRequest(w, r, "verify-der", err.Error())
		return
	}
	if verr := ber.VerifyDER(data, s.cfg.Limits); verr != nil {
		s.writeCodecErr(w, r, "verify-der", data, verr)
		return
	}
	s.writeOK(w, r, "verify-der", data, map[string]any{"der": true})
}

func (s *Server) decodeBody(w http.ResponseWriter, r *http.Request, v any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	if err := dec.Decode(v); err != nil {
		s.writeBadRequest(w, r, "parse", "invalid JSON body: "+err.Error())
		return false
	}
	return true
}

func (s *Server) writeOK(w http.ResponseWriter, r *http.Request, op string, input []byte, result any) {
	s.finish(w, r, http.StatusOK, op, input, envelope{OK: true, Result: result}, nil)
}

func (s *Server) writeCodecErr(w http.ResponseWriter, r *http.Request, op string, input []byte, e *ber.Error) {
	env := envelope{
		OK:    false,
		Error: &envelopeErr{Category: e.Category, Offset: e.Offset, Message: e.Msg},
	}
	s.finish(w, r, http.StatusUnprocessableEntity, op, input, env, e)
}

// writeBadRequest reports malformed request envelopes (bad JSON, bad hex,
// unknown parameters) as HTTP 400, distinct from codec errors (422).
func (s *Server) writeBadRequest(w http.ResponseWriter, r *http.Request, op string, msg string) {
	e := &ber.Error{Category: ber.CatSyntax, Offset: 0, Msg: "bad request: " + msg}
	env := envelope{
		OK:    false,
		Error: &envelopeErr{Category: e.Category, Offset: e.Offset, Message: e.Msg},
	}
	s.finish(w, r, http.StatusBadRequest, op, nil, env, e)
}

func (s *Server) finish(w http.ResponseWriter, r *http.Request, status int, op string, input []byte, env envelope, cause *ber.Error) {
	reqID := newRequestID()
	env.RunID = s.runID
	env.ReqID = reqID

	sum := sha256.Sum256(input)
	entry := store.Entry{
		RunID:       s.runID,
		RequestID:   reqID,
		Time:        time.Now(),
		Remote:      r.RemoteAddr,
		Op:          op,
		InputSHA256: hex.EncodeToString(sum[:]),
		InputBytes:  len(input),
		OK:          env.OK,
		Offset:      -1,
	}
	attrs := []any{
		"run_id", s.runID,
		"request_id", reqID,
		"op", op,
		"input_sha256", entry.InputSHA256,
		"input_bytes", entry.InputBytes,
		"ok", env.OK,
	}
	if cause != nil {
		entry.Category = string(cause.Category)
		entry.Offset = cause.Offset
		entry.Message = cause.Msg
		attrs = append(attrs,
			"category", string(cause.Category),
			"offset", cause.Offset,
			"message", cause.Msg,
		)
	}
	start, _ := r.Context().Value(startKey{}).(time.Time)
	entry.DurationUs = time.Since(start).Microseconds()
	attrs = append(attrs, "duration_us", entry.DurationUs)
	if s.st != nil {
		if err := s.st.Log(r.Context(), entry); err != nil {
			s.log.Error("audit log write failed", "error", err)
		}
	}
	s.log.Info("request", attrs...)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(env)
}
