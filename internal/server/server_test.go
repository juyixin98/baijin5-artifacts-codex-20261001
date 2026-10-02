package server

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"berconf/internal/ber"
	"berconf/internal/logx"
	"berconf/internal/store"
)

func testSvc(t *testing.T) *CodecService {
	t.Helper()
	st, err := store.Open("modernc-sqlite", "file::memory:?cache=shared", "request_log", 1)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	log, _ := logx.New("stderr", "text", logx.LevelError, "t")
	return &CodecService{Limits: ber.DefaultLimits(), Log: log, Store: st, Version: "t"}
}

func freePort(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()
	return addr
}

func TestLiveListenAndShutdown(t *testing.T) {
	addr := freePort(t)
	svc := testSvc(t)
	srv := New(svc, addr, time.Second, time.Second, 1<<20, svc.Log)

	serveErr := make(chan error, 1)
	go func() { serveErr <- srv.ListenAndServe() }()

	// wait for listener
	deadline := time.Now().Add(2 * time.Second)
	var up bool
	for time.Now().Before(deadline) {
		if c, err := net.Dial("tcp", addr); err == nil {
			_ = c.Close()
			up = true
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !up {
		t.Fatal("server never came up")
	}

	resp, err := http.Post("http://"+addr+"/v1/decode", "application/json",
		strings.NewReader(`{"input_hex":"020100"}`))
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		t.Fatalf("shutdown: %v", err)
	}
	if err := <-serveErr; err != nil {
		t.Fatalf("serve returned: %v", err)
	}
}

func TestBindFailureReported(t *testing.T) {
	// occupy a port, then fail to bind it again
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	svc := testSvc(t)
	srv := New(svc, ln.Addr().String(), time.Second, time.Second, 1<<20, svc.Log)
	if err := srv.ListenAndServe(); err == nil {
		t.Fatal("binding an occupied port must fail")
	}
}

func TestBodyLimitMiddleware(t *testing.T) {
	svc := testSvc(t)
	h := NewHandler(svc, 5, svc.Log)
	req := httptest.NewRequest(http.MethodPost, "/v1/decode", strings.NewReader(`{"input_hex":"020100"}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized body status = %d, want 413", rec.Code)
	}
	var env Envelope
	if err := json.Unmarshal(rec.Body.Bytes(), &env); err != nil {
		t.Fatal(err)
	}
	if env.Success || env.Error == nil || env.Error.Category != "REQUEST_TOO_LARGE" {
		t.Fatalf("unexpected envelope: %+v", env)
	}
}

func TestPanicRecoveryNotSuccess(t *testing.T) {
	log, _ := logx.New("stderr", "text", logx.LevelError, "t")
	panicker := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic("boom")
	})
	h := recoverMiddleware(log, panicker)
	req := httptest.NewRequest(http.MethodGet, "/x", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("panic status = %d, want 500", rec.Code)
	}
	body, _ := io.ReadAll(rec.Body)
	if strings.Contains(string(body), `"success":true`) {
		t.Fatal("panic must never be reported as success")
	}
}

func TestUnknownFieldsRejected(t *testing.T) {
	svc := testSvc(t)
	req := httptest.NewRequest(http.MethodPost, "/v1/encode",
		strings.NewReader(`{"encoding":"BER","node":{},"bogus":1}`))
	rec := httptest.NewRecorder()
	svc.HandleEncode(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("unknown field status = %d", rec.Code)
	}
}

func TestRootEndpointAndBasis(t *testing.T) {
	svc := testSvc(t)
	h := NewHandler(svc, 1<<20, svc.Log)
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("root status = %d", rec.Code)
	}
}
