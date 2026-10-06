package tests

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"pmd/service"
)

func newTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return httptest.NewServer(service.NewHandler(service.Default(), logger))
}

func fixtureSource(t *testing.T, rel string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "fixtures", rel))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	return string(b)
}

type envelope struct {
	RequestID string            `json:"request_id"`
	OK        bool              `json:"ok"`
	Versions  map[string]string `json:"versions"`
	Result    json.RawMessage   `json:"result"`
	Warnings  []string          `json:"warnings"`
	Uncertain []struct {
		Reason string `json:"reason"`
	} `json:"uncertain"`
	Error *struct {
		Category string `json:"category"`
		Message  string `json:"message"`
		Position string `json:"position"`
	} `json:"error"`
}

func post(t *testing.T, url, body, reqID string) (*http.Response, envelope) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, url, strings.NewReader(body))
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if reqID != "" {
		req.Header.Set("X-Request-ID", reqID)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("post %s: %v", url, err)
	}
	defer resp.Body.Close()
	var env envelope
	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
		t.Fatalf("decode envelope: %v", err)
	}
	return resp, env
}

func TestServiceHealth(t *testing.T) {
	srv := newTestServer(t)
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/v1/health")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	var env envelope
	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !env.OK {
		t.Error("health not ok")
	}
	for _, mod := range []string{"service", "frontend", "ir", "runtime", "diff"} {
		if env.Versions[mod] == "" {
			t.Errorf("versions missing %q: %v", mod, env.Versions)
		}
	}
	if resp.Header.Get("X-Request-ID") == "" {
		t.Error("missing X-Request-ID response header")
	}
}

func TestServiceMatchWithVerification(t *testing.T) {
	srv := newTestServer(t)
	defer srv.Close()
	src := fixtureSource(t, "programs/list.pmd")
	value := `{"ctor":"Cons","args":[{"lit":2},{"ctor":"Cons","args":[{"lit":2},{"ctor":"Nil"}]}]}`
	body := `{"source":` + strconv.Quote(src) + `,"value":` + value + `}`
	resp, env := post(t, srv.URL+"/v1/match", body, "test-req-7")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	if !env.OK {
		t.Fatalf("not ok: %+v", env.Error)
	}
	if env.RequestID != "test-req-7" {
		t.Errorf("request_id = %q, want test-req-7", env.RequestID)
	}
	if resp.Header.Get("X-Request-ID") != "test-req-7" {
		t.Errorf("X-Request-ID header = %q", resp.Header.Get("X-Request-ID"))
	}
	var result struct {
		Outcome struct {
			Matched bool   `json:"matched"`
			Branch  int    `json:"branch"`
			Label   string `json:"label"`
			Effects []struct {
				Label string `json:"label"`
			} `json:"effects"`
			Steps []string `json:"steps"`
		} `json:"outcome"`
		Verification struct {
			Agree bool `json:"agree"`
		} `json:"verification"`
	}
	if err := json.Unmarshal(env.Result, &result); err != nil {
		t.Fatalf("unmarshal result: %v", err)
	}
	if !result.Outcome.Matched || result.Outcome.Branch != 0 || result.Outcome.Label != "pair-eq" {
		t.Errorf("outcome = %+v, want branch 0 pair-eq", result.Outcome)
	}
	if len(result.Outcome.Effects) != 1 || result.Outcome.Effects[0].Label != "pair-check" {
		t.Errorf("effects = %+v, want [pair-check]", result.Outcome.Effects)
	}
	if !result.Verification.Agree {
		t.Error("verification did not agree")
	}
	if len(result.Outcome.Steps) == 0 {
		t.Error("empty step trace")
	}
}

func TestServiceErrorSemantics(t *testing.T) {
	srv := newTestServer(t)
	defer srv.Close()

	// Parse error: bad token.
	_, env := post(t, srv.URL+"/v1/match", `{"source":"match v { | A = \"b\" }","value":{"lit":1}}`, "")
	if env.OK || env.Error == nil || env.Error.Category != "parse_error" {
		t.Errorf("parse error case: %+v", env.Error)
	}
	// Semantic error: unbound guard variable, with a source position.
	bad := fixtureSource(t, "programs/bad_unbound.pmd")
	resp, env := post(t, srv.URL+"/v1/match", `{"source":`+strconv.Quote(bad)+`,"value":{"ctor":"A","args":[{"lit":1}]}}`, "")
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("semantic case status = %d, want 400", resp.StatusCode)
	}
	if env.OK || env.Error == nil || env.Error.Category != "semantic_error" || env.Error.Position == "" {
		t.Errorf("semantic error case: %+v", env.Error)
	}
	// Runtime failure is a result, not an API error: ok=true with failure category.
	src := fixtureSource(t, "programs/list.pmd")
	_, env = post(t, srv.URL+"/v1/match", `{"source":`+strconv.Quote(src)+`,"value":{"ctor":"Cons","args":[{"lit":1}]}}`, "")
	if !env.OK {
		t.Fatalf("invalid value should still be ok=true: %+v", env.Error)
	}
	var result struct {
		Outcome struct {
			Failure *struct {
				Category string `json:"category"`
			} `json:"failure"`
		} `json:"outcome"`
	}
	if err := json.Unmarshal(env.Result, &result); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if result.Outcome.Failure == nil || result.Outcome.Failure.Category != "invalid_value" {
		t.Errorf("failure = %+v, want invalid_value", result.Outcome.Failure)
	}
	// Malformed JSON body.
	resp, err := http.Post(srv.URL+"/v1/match", "application/json", strings.NewReader("{not json"))
	if err != nil {
		t.Fatalf("post: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("bad json status = %d, want 400", resp.StatusCode)
	}
	// Unknown route.
	resp, err = http.Get(srv.URL + "/nope")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("unknown route status = %d, want 404", resp.StatusCode)
	}
}

func TestServiceCompileWarnings(t *testing.T) {
	srv := newTestServer(t)
	defer srv.Close()
	src := "ctor A 0\nctor B 0\n\nmatch v {\n  | _ => \"wild\"\n  | A => \"a\"\n}\n"
	_, env := post(t, srv.URL+"/v1/compile", `{"source":`+strconv.Quote(src)+`}`, "")
	if !env.OK {
		t.Fatalf("not ok: %+v", env.Error)
	}
	if len(env.Warnings) != 1 || !strings.Contains(env.Warnings[0], "branch 1") {
		t.Errorf("warnings = %v, want one about branch 1", env.Warnings)
	}
}

func TestServiceDiff(t *testing.T) {
	srv := newTestServer(t)
	defer srv.Close()
	src := fixtureSource(t, "programs/guards.pmd")
	_, env := post(t, srv.URL+"/v1/diff", `{"source":`+strconv.Quote(src)+`,"cases":100,"seed":5}`, "")
	if !env.OK {
		t.Fatalf("not ok: %+v", env.Error)
	}
	var rep struct {
		Compared   int `json:"compared"`
		Equal      int `json:"equal"`
		Mismatches []struct {
			Reason string `json:"reason"`
		} `json:"mismatches"`
	}
	if err := json.Unmarshal(env.Result, &rep); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if rep.Compared < 100 {
		t.Errorf("compared = %d, want >= 100", rep.Compared)
	}
	if len(rep.Mismatches) != 0 {
		t.Errorf("mismatches = %+v", rep.Mismatches)
	}
	if rep.Equal != rep.Compared {
		t.Errorf("equal = %d, compared = %d", rep.Equal, rep.Compared)
	}
}
