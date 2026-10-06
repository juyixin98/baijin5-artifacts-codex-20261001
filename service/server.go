package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"sync/atomic"
	"time"

	"pmd/diff"
	"pmd/frontend"
	"pmd/ir"
	"pmd/runtime"
)

// Server holds the HTTP handler state.
type Server struct {
	cfg Config
	log *slog.Logger
	seq atomic.Int64
}

type ctxKey struct{}

// NewHandler builds the HTTP handler with request-ID and logging
// middleware installed.
func NewHandler(cfg Config, log *slog.Logger) http.Handler {
	s := &Server{cfg: cfg, log: log}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/health", s.health)
	mux.HandleFunc("POST /v1/compile", s.compile)
	mux.HandleFunc("POST /v1/match", s.match)
	mux.HandleFunc("POST /v1/diff", s.diff)
	return s.withRequestID(s.withLogging(mux))
}

func (s *Server) withRequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-ID")
		if id == "" {
			id = fmt.Sprintf("req-%d", s.seq.Add(1))
		}
		w.Header().Set("X-Request-ID", id)
		ctx := context.WithValue(r.Context(), ctxKey{}, id)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

func (s *Server) withLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(sw, r)
		s.log.Info("request",
			"request_id", requestID(r),
			"method", r.Method,
			"path", r.URL.Path,
			"status", sw.status,
			"dur_ms", time.Since(start).Milliseconds(),
		)
	})
}

func requestID(r *http.Request) string {
	if id, ok := r.Context().Value(ctxKey{}).(string); ok {
		return id
	}
	return "unknown"
}

// versions reports every module version so responses and logs can be
// tied back to the exact processing pipeline.
func versions() map[string]string {
	return map[string]string{
		"service":  Version,
		"frontend": frontend.Version,
		"ir":       ir.Version,
		"runtime":  runtime.Version,
		"diff":     diff.Version,
	}
}

type apiError struct {
	Category string `json:"category"`
	Message  string `json:"message"`
	Position string `json:"position,omitempty"`
}

type envelope struct {
	RequestID string            `json:"request_id"`
	OK        bool              `json:"ok"`
	Versions  map[string]string `json:"versions"`
	Result    any               `json:"result,omitempty"`
	Warnings  []string          `json:"warnings,omitempty"`
	Uncertain []diff.Uncertain  `json:"uncertain,omitempty"`
	Error     *apiError         `json:"error,omitempty"`
}

func (s *Server) writeEnv(w http.ResponseWriter, reqID string, status int, env envelope) {
	env.RequestID = reqID
	env.Versions = versions()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	if err := enc.Encode(env); err != nil {
		s.log.Error("encode response", "request_id", reqID, "error", err)
	}
}

func (s *Server) fail(w http.ResponseWriter, reqID string, status int, ae *apiError) {
	s.log.Info("request failed",
		"request_id", reqID,
		"category", ae.Category,
		"message", ae.Message,
		"position", ae.Position,
	)
	s.writeEnv(w, reqID, status, envelope{OK: false, Error: ae})
}

func (s *Server) decode(w http.ResponseWriter, r *http.Request, reqID string, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, s.cfg.MaxBodyBytes)
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(v); err != nil {
		s.fail(w, reqID, http.StatusBadRequest, &apiError{
			Category: CatBadRequest,
			Message:  "invalid JSON body: " + err.Error(),
		})
		return false
	}
	return true
}

// parseProgram runs the frontend and converts its categorized errors.
func (s *Server) parseProgram(reqID, src string) (*frontend.Program, *apiError) {
	if src == "" {
		return nil, &apiError{Category: CatBadRequest, Message: "source is required"}
	}
	prog, err := frontend.Parse(src)
	if err != nil {
		var fe *frontend.Error
		if errors.As(err, &fe) {
			ae := &apiError{Category: fe.Category, Message: fe.Message}
			if fe.Pos != nil {
				ae.Position = fe.Pos.String()
			}
			return nil, ae
		}
		return nil, &apiError{Category: CatInternal, Message: err.Error()}
	}
	s.log.Info("program parsed",
		"request_id", reqID,
		"module", "frontend",
		"module_version", frontend.Version,
		"branches", len(prog.Branches),
		"ctors", len(prog.Ctors),
	)
	return prog, nil
}

func (s *Server) compileTree(reqID string, prog *frontend.Program) *ir.Tree {
	tr := ir.Compile(prog)
	s.log.Info("program compiled",
		"request_id", reqID,
		"module", "ir",
		"module_version", ir.Version,
		"nodes", tr.Stats.Nodes,
		"switches", tr.Stats.Switches,
		"guards", tr.Stats.Guards,
		"warnings", len(tr.Warnings),
	)
	return tr
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	s.writeEnv(w, requestID(r), http.StatusOK, envelope{
		OK: true,
		Result: map[string]any{
			"status": "ok",
			"time":   time.Now().UTC().Format(time.RFC3339),
		},
	})
}
