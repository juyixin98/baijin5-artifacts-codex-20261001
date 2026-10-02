package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func postRec(t *testing.T, h http.Handler, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestValidateRequiresMode(t *testing.T) {
	svc := testSvc(t)
	rec := postRec(t, NewHandler(svc, 1<<20, svc.Log),
		"/v1/validate", `{"input_hex":"020100"}`)
	if rec.Code != http.StatusBadRequest ||
		!strings.Contains(rec.Body.String(), "INVALID_REQUEST") {
		t.Fatalf("missing mode: %d %s", rec.Code, rec.Body.String())
	}
}

func TestEncodeBadEncodingAndNode(t *testing.T) {
	svc := testSvc(t)
	h := NewHandler(svc, 1<<20, svc.Log)

	rec := postRec(t, h, "/v1/encode", `{"encoding":"XML","node":{}}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "INVALID_REQUEST") {
		t.Fatalf("bad encoding: %d %s", rec.Code, rec.Body.String())
	}

	// primitive with invalid hex content
	rec = postRec(t, h, "/v1/encode", `{"encoding":"BER","node":{
		"class":"universal","tag":2,"constructed":false,"value_hex":"zz"}}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("bad node hex: %d %s", rec.Code, rec.Body.String())
	}

	// application class is outside the profile, rejected at request validation
	rec = postRec(t, h, "/v1/encode", `{"encoding":"BER","node":{
		"class":"application","tag":2,"constructed":false,"value_hex":"00"}}`)
	if rec.Code != http.StatusBadRequest ||
		!strings.Contains(rec.Body.String(), "INVALID_REQUEST") {
		t.Fatalf("application class: %d %s", rec.Code, rec.Body.String())
	}
}

func TestDecodeUnsupportedReturns422(t *testing.T) {
	svc := testSvc(t)
	rec := postRec(t, NewHandler(svc, 1<<20, svc.Log),
		"/v1/decode", `{"input_hex":"06012a"}`) // OID, outside profile
	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("unsupported tag status = %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "UNSUPPORTED") {
		t.Fatalf("body = %s", rec.Body.String())
	}
}

func TestNonJSONBodyIsBadRequest(t *testing.T) {
	svc := testSvc(t)
	rec := postRec(t, NewHandler(svc, 1<<20, svc.Log), "/v1/decode", "not-json")
	if rec.Code != http.StatusBadRequest ||
		!strings.Contains(rec.Body.String(), "INVALID_REQUEST") {
		t.Fatalf("non-json body: %d %s", rec.Code, rec.Body.String())
	}
}
