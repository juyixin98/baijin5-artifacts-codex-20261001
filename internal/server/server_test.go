package server_test

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"berd/internal/ber"
	"berd/internal/config"
	"berd/internal/harness"
	"berd/internal/server"
	"berd/internal/store"
)

type envelope struct {
	OK     bool            `json:"ok"`
	Result json.RawMessage `json:"result"`
	Error  *struct {
		Category string `json:"category"`
		Offset   int    `json:"offset"`
		Message  string `json:"message"`
	} `json:"error"`
	RunID string `json:"run_id"`
	ReqID string `json:"request_id"`
}

type fixture struct {
	ts  *httptest.Server
	st  *store.Store
	srv *server.Server
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	cfg := config.Config{
		Listen: "127.0.0.1:0",
		DBPath: filepath.Join(t.TempDir(), "audit.db"),
		Limits: ber.DefaultLimits(),
	}
	st, err := store.Open(cfg.DBPath)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	srv := server.New(cfg, st, slog.New(slog.NewTextHandler(io.Discard, nil)))
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)
	return &fixture{ts: ts, st: st, srv: srv}
}

func (f *fixture) post(t *testing.T, path string, body string) (int, envelope) {
	t.Helper()
	resp, err := http.Post(f.ts.URL+path, "application/json", bytes.NewBufferString(body))
	if err != nil {
		t.Fatalf("POST %s: %v", path, err)
	}
	defer resp.Body.Close()
	var env envelope
	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return resp.StatusCode, env
}

func (f *fixture) get(t *testing.T, path string) (int, envelope) {
	t.Helper()
	resp, err := http.Get(f.ts.URL + path)
	if err != nil {
		t.Fatalf("GET %s: %v", path, err)
	}
	defer resp.Body.Close()
	var env envelope
	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return resp.StatusCode, env
}

func TestHealthReportsIdentity(t *testing.T) {
	h := harness.New(t)
	f := newFixture(t)
	status, env := f.get(t, "/v1/health")
	h.ExpectEqual("status", status, http.StatusOK)
	if !env.OK {
		t.Fatalf("health not ok: %s", env.Result)
	}
	var result map[string]any
	if err := json.Unmarshal(env.Result, &result); err != nil {
		t.Fatal(err)
	}
	h.ExpectEqual("run_id matches server", result["run_id"], f.srv.RunID())
	h.ExpectEqual("version", result["version"], "1.0.0")
	if result["go_version"] == "" {
		t.Fatal("go_version missing")
	}
	h.Step("verdict=PASS basis=health exposes run identity and versions")
}

func TestDecodeEndpoint(t *testing.T) {
	h := harness.New(t)
	f := newFixture(t)

	status, env := f.post(t, "/v1/decode", `{"data":"020105"}`)
	h.ExpectEqual("status", status, http.StatusOK)
	var result struct {
		Value struct {
			Type  string `json:"type"`
			Value string `json:"value"`
		} `json:"value"`
	}
	if err := json.Unmarshal(env.Result, &result); err != nil {
		t.Fatal(err)
	}
	h.ExpectEqual("type", result.Value.Type, "INTEGER")
	h.ExpectEqual("value", result.Value.Value, "5")

	// Nested indefinite-length SEQUENCE decodes to the same tree.
	status, env = f.post(t, "/v1/decode", `{"data":"3080308002010500000000"}`)
	h.ExpectEqual("status", status, http.StatusOK)
	var nested struct {
		Value struct {
			Type       string `json:"type"`
			Indefinite bool   `json:"indefinite"`
			Children   []struct {
				Indefinite bool `json:"indefinite"`
				Children   []struct {
					Value string `json:"value"`
				} `json:"children"`
			} `json:"children"`
		} `json:"value"`
	}
	if err := json.Unmarshal(env.Result, &nested); err != nil {
		t.Fatal(err)
	}
	h.ExpectEqual("outer type", nested.Value.Type, "SEQUENCE")
	h.ExpectEqual("outer indefinite", nested.Value.Indefinite, true)
	h.ExpectEqual("inner indefinite", nested.Value.Children[0].Indefinite, true)
	h.ExpectEqual("leaf value", nested.Value.Children[0].Children[0].Value, "5")
}

func TestDecodeErrorsAreClassified(t *testing.T) {
	f := newFixture(t)
	cases := []struct {
		name     string
		body     string
		status   int
		category string
		offset   int
	}{
		{"truncation", `{"data":"0201"}`, 422, "truncation", 2},
		{"eoc-bare", `{"data":"0000"}`, 422, "eoc", 0},
		{"bad-hex", `{"data":"zz"}`, 400, "syntax", 0},
		{"bad-json", `{`, 400, "syntax", 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := harness.New(t)
			status, env := f.post(t, "/v1/decode", tc.body)
			h.ExpectEqual("status", status, tc.status)
			if env.OK {
				t.Fatalf("error case %s returned ok=true", tc.name)
			}
			if env.Error == nil {
				t.Fatalf("error case %s has no error object", tc.name)
			}
			h.ExpectEqual("category", env.Error.Category, tc.category)
			h.ExpectEqual("offset", env.Error.Offset, tc.offset)
		})
	}
}

// TestOversizedIntegerRejected sends a real 1025-byte INTEGER, exceeding the
// default 1024-byte integer limit, and expects a resource error at the
// content offset.
func TestOversizedIntegerRejected(t *testing.T) {
	h := harness.New(t)
	f := newFixture(t)
	// 02 82 04 01 followed by 1025 content octets.
	body := `{"data":"02820401` + string(bytes.Repeat([]byte("41"), 1025)) + `"}`
	status, env := f.post(t, "/v1/decode", body)
	h.ExpectEqual("status", status, 422)
	if env.Error == nil {
		t.Fatal("expected error object")
	}
	h.ExpectEqual("category", env.Error.Category, "resource")
	h.ExpectEqual("offset", env.Error.Offset, 4)
}

func TestEncodeEndpoint(t *testing.T) {
	h := harness.New(t)
	f := newFixture(t)
	status, env := f.post(t, "/v1/encode",
		`{"form":"der","spec":{"type":"sequence","children":[{"type":"integer","value":"5"},{"type":"integer","value":"-129"}]}}`)
	h.ExpectEqual("status", status, http.StatusOK)
	var result struct {
		Hex string `json:"hex"`
	}
	if err := json.Unmarshal(env.Result, &result); err != nil {
		t.Fatal(err)
	}
	h.ExpectBytes("encoded DER", mustHex(t, result.Hex), "30070201050202ff7f")
}

func TestCanonicalizeEndpoint(t *testing.T) {
	h := harness.New(t)
	f := newFixture(t)
	status, env := f.post(t, "/v1/canonicalize", `{"data":"30800201050000"}`)
	h.ExpectEqual("status", status, http.StatusOK)
	var result struct {
		DERHex  string `json:"der_hex"`
		Changed bool   `json:"changed"`
	}
	if err := json.Unmarshal(env.Result, &result); err != nil {
		t.Fatal(err)
	}
	h.ExpectEqual("der", result.DERHex, "3003020105")
	h.ExpectEqual("changed", result.Changed, true)
}

func TestVerifyDEREndpoint(t *testing.T) {
	h := harness.New(t)
	f := newFixture(t)

	status, env := f.post(t, "/v1/verify-der", `{"data":"02017f"}`)
	h.ExpectEqual("status", status, http.StatusOK)
	if !env.OK {
		t.Fatal("canonical DER rejected")
	}

	status, env = f.post(t, "/v1/verify-der", `{"data":"0202007f"}`)
	h.ExpectEqual("status", status, 422)
	if env.Error == nil {
		t.Fatal("expected error object")
	}
	h.ExpectEqual("category", env.Error.Category, "constraint")
	h.ExpectEqual("offset", env.Error.Offset, 1)
}

// TestAuditLogRecordsEveryRequest checks that every API call — success and
// failure alike — lands in the SQLite audit log under the server's run ID.
func TestAuditLogRecordsEveryRequest(t *testing.T) {
	h := harness.New(t)
	f := newFixture(t)

	f.get(t, "/v1/health")
	f.get(t, "/v1/config")
	f.post(t, "/v1/decode", `{"data":"020105"}`)
	f.post(t, "/v1/decode", `{"data":"0201"}`) // codec error
	f.post(t, "/v1/decode", `{"data":"zz"}`)   // bad request
	f.post(t, "/v1/verify-der", `{"data":"3000"}`)

	n, err := f.st.Count(context.Background(), f.srv.RunID())
	if err != nil {
		t.Fatalf("count: %v", err)
	}
	h.ExpectEqual("audit rows for run", n, 6)
	h.Step("verdict=PASS basis=success and failure requests both audited with run_id=%s", f.srv.RunID())
}

func TestEncodeAndCanonicalizeErrors(t *testing.T) {
	f := newFixture(t)
	cases := []struct {
		name     string
		path     string
		body     string
		status   int
		category string
	}{
		{"encode-unknown-form", "/v1/encode",
			`{"form":"cer","spec":{"type":"integer","value":"1"}}`, 400, "syntax"},
		{"encode-bad-spec", "/v1/encode",
			`{"form":"der","spec":{"type":"integer","value":"abc"}}`, 422, "syntax"},
		{"encode-unknown-type", "/v1/encode",
			`{"spec":{"type":"octetstring"}}`, 422, "syntax"},
		{"canonicalize-truncated", "/v1/canonicalize",
			`{"data":"0201"}`, 422, "truncation"},
		{"canonicalize-bad-hex", "/v1/canonicalize",
			`{"data":"xy"}`, 400, "syntax"},
		{"verify-der-bad-hex", "/v1/verify-der",
			`{"data":"xy"}`, 400, "syntax"},
		{"decode-base64", "/v1/decode",
			`{"encoding":"base64","data":"AgEF"}`, 200, ""},
		{"decode-unknown-encoding", "/v1/decode",
			`{"encoding":"rot13","data":"0201"}`, 400, "syntax"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := harness.New(t)
			status, env := f.post(t, tc.path, tc.body)
			h.ExpectEqual("status", status, tc.status)
			if tc.status == http.StatusOK {
				if !env.OK {
					t.Fatal("expected ok=true")
				}
				return
			}
			if env.OK || env.Error == nil {
				t.Fatalf("expected classified error, got ok=%v", env.OK)
			}
			h.ExpectEqual("category", env.Error.Category, tc.category)
		})
	}
}

func TestConfigEndpoint(t *testing.T) {
	h := harness.New(t)
	f := newFixture(t)
	status, env := f.get(t, "/v1/config")
	h.ExpectEqual("status", status, http.StatusOK)
	var result struct {
		Limits struct {
			MaxDepth        int  `json:"max_depth"`
			AllowIndefinite bool `json:"allow_indefinite"`
		} `json:"limits"`
	}
	if err := json.Unmarshal(env.Result, &result); err != nil {
		t.Fatal(err)
	}
	h.ExpectEqual("max_depth", result.Limits.MaxDepth, 32)
	h.ExpectEqual("allow_indefinite", result.Limits.AllowIndefinite, true)
}

func mustHex(t *testing.T, s string) []byte {
	t.Helper()
	b, err := hex.DecodeString(s)
	if err != nil {
		t.Fatalf("hex: %v", err)
	}
	return b
}
