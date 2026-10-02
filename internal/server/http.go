package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"sync/atomic"
	"time"

	"berconf/internal/logx"
)

// Server bundles the HTTP listener with its lifecycle.
type Server struct {
	httpSrv *http.Server
	log     *logx.Logger
	ready   atomic.Bool
}

// New wires routes and bounded middleware.
func New(svc *CodecService, addr string, readTO, writeTO time.Duration, maxBody int64, log *logx.Logger) *Server {
	handler := NewHandler(svc, maxBody, log)
	s := &Server{
		httpSrv: &http.Server{
			Addr:         addr,
			Handler:      handler,
			ReadTimeout:  readTO,
			WriteTimeout: writeTO,
		},
		log: log,
	}
	s.ready.Store(true)
	return s
}

// NewHandler builds the fully-wired HTTP handler (routes plus the body,
// request-log and panic-recovery middleware).
func NewHandler(svc *CodecService, maxBody int64, log *logx.Logger) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/decode", svc.HandleDecode)
	mux.HandleFunc("POST /v1/encode", svc.HandleEncode)
	mux.HandleFunc("POST /v1/validate", svc.HandleValidate)
	mux.HandleFunc("GET /healthz", svc.HandleHealth)
	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		svc.write(w, http.StatusOK, Envelope{
			Success: true,
			Data: map[string]any{
				"service": "ber-restricted-codec",
				"version": svc.Version,
				"endpoints": []string{
					"POST /v1/decode", "POST /v1/encode", "POST /v1/validate", "GET /healthz",
				},
			},
		})
	})
	return recoverMiddleware(log, bodyLimitMiddleware(maxBody, log, requestLogMiddleware(log, mux)))
}

// ListenAndServe starts serving. The listener is bound before ready.
func (s *Server) ListenAndServe() error {
	ln, err := net.Listen("tcp", s.httpSrv.Addr)
	if err != nil {
		return fmt.Errorf("bind %s: %w", s.httpSrv.Addr, err)
	}
	s.log.Info("http listening", map[string]any{"addr": s.httpSrv.Addr})
	if err := s.httpSrv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

// Shutdown performs a bounded graceful stop.
func (s *Server) Shutdown(ctx context.Context) error {
	s.ready.Store(false)
	return s.httpSrv.Shutdown(ctx)
}

func bodyLimitMiddleware(max int64, log *logx.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ContentLength > max {
			reject(w, http.StatusRequestEntityTooLarge, "REQUEST_TOO_LARGE",
				fmt.Sprintf("request body %d exceeds limit %d", r.ContentLength, max))
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, max)
		next.ServeHTTP(w, r)
	})
}

func requestLogMiddleware(log *logx.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		sw := &statusWriter{ResponseWriter: w, status: 200}
		next.ServeHTTP(sw, r)
		log.Info("http request", map[string]any{
			"method": r.Method, "path": r.URL.Path,
			"status": sw.status, "duration_us": time.Since(start).Microseconds(),
			"remote": r.RemoteAddr,
		})
	})
}

func recoverMiddleware(log *logx.Logger, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				log.Error("panic recovered", map[string]any{
					"path": r.URL.Path, "panic": fmt.Sprint(rec),
				})
				// Unknown/exceptional state must never look like success.
				reject(w, http.StatusInternalServerError, "INTERNAL",
					"internal error; the request was not completed")
			}
		}()
		next.ServeHTTP(w, r)
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

func reject(w http.ResponseWriter, status int, category, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(Envelope{
		Success: false,
		Error:   &ErrorDetail{Category: category, Message: msg},
	})
}
