// Package integration_test exercises the controlled HTTP service
// end-to-end against an in-memory SQLite store: request gating, the
// response envelope, exact error categories/offsets over the wire,
// audit persistence and run-id correlation. No network ports outside
// the loopback interface are used.
package integration_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"berconf/internal/ber"
	"berconf/internal/logx"
	"berconf/internal/server"
	"berconf/internal/store"
)

const version = "test-1.0.0"

func newService(t *testing.T) (*server.CodecService, *store.Store) {
	t.Helper()
	st, err := store.Open("modernc-sqlite", "file::memory:?cache=shared", "request_log", 1)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	log, _ := logx.New("stderr", "json", logx.LevelError, version)
	return &server.CodecService{
		Limits:  testLimits(),
		Log:     log,
		Store:   st,
		Version: version,
	}, st
}

func testLimits() ber.Limits {
	l := ber.DefaultLimits()
	l.MaxDepth = 8
	l.MaxContentBytes = 8192
	l.MaxIntegerBytes = 256
	return l
}

func serve(t *testing.T, maxBody int64) (*httptest.Server, *store.Store) {
	t.Helper()
	svc, st := newService(t)
	ts := httptest.NewServer(server.NewHandler(svc, maxBody, svc.Log))
	t.Cleanup(ts.Close)
	return ts, st
}

func postJSON(t *testing.T, ts *httptest.Server, path string, body string) (int, map[string]any) {
	t.Helper()
	resp, err := http.Post(ts.URL+path, "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatalf("http: %v", err)
	}
	defer resp.Body.Close()
	var env map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return resp.StatusCode, env
}

func TestDecodeSuccessEnvelope(t *testing.T) {
	ts, _ := serve(t, 1<<20)
	status, env := postJSON(t, ts, "/v1/decode", `{"input_hex":"3006020101020102","mode":"BER"}`)
	if status != http.StatusOK {
		t.Fatalf("status = %d, env = %v", status, env)
	}
	if ok, _ := env["success"].(bool); !ok {
		t.Fatalf("success not true: %v", env)
	}
	data := env["data"].(map[string]any)
	node := data["node"].(map[string]any)
	if node["class"] != "universal" || node["tag"].(float64) != 16 {
		t.Fatalf("unexpected node: %v", node)
	}
	meta := env["meta"].(map[string]any)
	if meta["run_id"] == nil || len(meta["run_id"].(string)) < 16 {
		t.Fatalf("run id missing: %v", meta)
	}
	if meta["version"] != version {
		t.Fatalf("version = %v", meta["version"])
	}
	if _, ok := meta["decision_basis"].(string); !ok {
		t.Fatalf("decision basis missing: %v", meta)
	}
}

func TestDecodeErrorCarriesCategoryAndOffset(t *testing.T) {
	ts, _ := serve(t, 1<<20)
	// misplaced EOC at offset 2
	status, env := postJSON(t, ts, "/v1/decode", `{"input_hex":"300400000200"}`)
	if status != http.StatusBadRequest {
		t.Fatalf("status = %d want 400", status)
	}
	if success, _ := env["success"].(bool); success {
		t.Fatal("failure reported as success")
	}
	errObj := env["error"].(map[string]any)
	if errObj["category"] != "MALFORMED_EOC" {
		t.Fatalf("category = %v", errObj["category"])
	}
	if off := errObj["offset"].(float64); off != 2 {
		t.Fatalf("offset = %v, want 2", off)
	}
	if _, has := errObj["offset"]; !has {
		t.Fatal("offset must be present for byte-level failures")
	}
}

func TestDERModeRejectsIndefinite(t *testing.T) {
	ts, _ := serve(t, 1<<20)
	status, env := postJSON(t, ts, "/v1/validate", `{"input_hex":"30800000","mode":"DER"}`)
	if status != http.StatusBadRequest {
		t.Fatalf("status = %d", status)
	}
	if env["error"].(map[string]any)["category"] != "INVALID_ENCODING" {
		t.Fatalf("env = %v", env)
	}
	// same bytes pass BER validation
	status, env = postJSON(t, ts, "/v1/validate", `{"input_hex":"30800000","mode":"BER"}`)
	if status != http.StatusOK || !env["success"].(bool) {
		t.Fatalf("BER validation should pass: %d %v", status, env)
	}
}

func TestInvalidHexIsRejectedNotSuccess(t *testing.T) {
	ts, _ := serve(t, 1<<20)
	status, env := postJSON(t, ts, "/v1/decode", `{"input_hex":"zz"}`)
	if status != http.StatusBadRequest || env["error"].(map[string]any)["category"] != "INVALID_HEX" {
		t.Fatalf("invalid hex: %d %v", status, env)
	}
	status, env = postJSON(t, ts, "/v1/decode", `{"input_hex":"abc"}`)
	if status != http.StatusBadRequest || env["error"].(map[string]any)["category"] != "INVALID_HEX" {
		t.Fatalf("odd-length hex: %d %v", status, env)
	}
}

func TestResourceLimitMapsTo413(t *testing.T) {
	ts, _ := serve(t, 1<<20)
	// MaxIntegerBytes=256: 300-byte integer content
	var b bytes.Buffer
	b.WriteString("0282012d00")
	b.WriteString(strings.Repeat("ff", 300))
	status, env := postJSON(t, ts, "/v1/decode", `{"input_hex":"`+b.String()+`"}`)
	if status != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d want 413, env=%v", status, env)
	}
	if env["error"].(map[string]any)["category"] != "SIZE_EXCEEDED" {
		t.Fatalf("category = %v", env["error"])
	}
}

func TestAuditRowCorrelatesByRunID(t *testing.T) {
	ts, st := serve(t, 1<<20)
	_, env := postJSON(t, ts, "/v1/decode", `{"input_hex":"02010142"}`) // trailing bytes -> error
	runID := env["meta"].(map[string]any)["run_id"].(string)

	rows, err := st.Recent(10)
	if err != nil {
		t.Fatalf("query audit: %v", err)
	}
	var found bool
	for _, r := range rows {
		if r.RunID == runID {
			found = true
			if r.Status != "error" || r.ErrorKind != "TRAILING_DATA" || r.ErrorOffset != 3 {
				t.Fatalf("audit row mismatch: %+v", r)
			}
			if r.InputSHA256 == "" || r.InputLen != 4 {
				t.Fatalf("audit row missing input identity: %+v", r)
			}
		}
	}
	if !found {
		t.Fatalf("no audit row for run id %s", runID)
	}
}

func TestHealthReflectsStore(t *testing.T) {
	ts, _ := serve(t, 1<<20)
	resp, err := http.Get(ts.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var env map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK || !env["success"].(bool) {
		t.Fatalf("health: %d %v", resp.StatusCode, env)
	}
}

func TestEncodeEndpointRoundTrip(t *testing.T) {
	ts, _ := serve(t, 1<<20)
	body := `{"encoding":"DER","node":{
		"class":"universal","tag":16,"constructed":true,
		"children":[
			{"class":"universal","tag":2,"constructed":false,"value_hex":"01"},
			{"class":"context","tag":0,"constructed":true,
			 "children":[{"class":"universal","tag":2,"constructed":false,"value_hex":"02"}]}
		]}}`
	status, env := postJSON(t, ts, "/v1/encode", body)
	if status != http.StatusOK {
		t.Fatalf("encode: %d %v", status, env)
	}
	out := env["data"].(map[string]any)["output_hex"].(string)
	if out != "3008020101a003020102" {
		t.Fatalf("encoded = %s", out)
	}
}

func TestEncodeIndefiniteOverWire(t *testing.T) {
	ts, _ := serve(t, 1<<20)
	body := `{"encoding":"BER_INDEFINITE","node":{
		"class":"universal","tag":16,"constructed":true,
		"children":[{"class":"universal","tag":2,"constructed":false,"value_hex":"05"}]}}`
	status, env := postJSON(t, ts, "/v1/encode", body)
	if status != http.StatusOK {
		t.Fatalf("encode: %d %v", status, env)
	}
	out := env["data"].(map[string]any)["output_hex"].(string)
	if out != "30800201050000" {
		t.Fatalf("indefinite encoding = %s, want 30800201050000", out)
	}
}
