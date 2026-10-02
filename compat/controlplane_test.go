package compat_test

import (
	"encoding/json"
	"net/http"
	"testing"
)

// TestControlPlaneHealthAndVersion covers the operational endpoints that
// make a run explainable: health, version/config, live stats.
func TestControlPlaneHealthAndVersion(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	// /healthz
	getJSON(t, "http://"+h.control+"/healthz", nil)

	// /version
	var version map[string]any
	getJSON(t, "http://"+h.control+"/version", &version)
	if version["version"] != "modbus-fixture-1.0.0" {
		t.Fatalf("version = %v", version["version"])
	}
	if version["latency"] != configLatencyNone {
		t.Fatalf("latency = %v", version["latency"])
	}

	// /stats initially has zero successful requests.
	var stats map[string]any
	getJSON(t, "http://"+h.control+"/stats", &stats)
	if _, ok := stats["requests_ok"]; !ok {
		t.Fatalf("stats missing requests_ok: %v", stats)
	}
}

// TestControlPlaneAuditAndStatsAfterTraffic drives one normal request and
// one exception, then asserts the audit trail and counters classify them.
func TestControlPlaneAuditAndStatsAfterTraffic(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	// One good FC03 read through a raw connection.
	okConn := h.dial(t)
	writeFull(t, okConn, readReq(0x1111, 0x01, 0, 2), 0)
	readExactFrame(t, okConn)
	_ = okConn.Close()

	// One illegal-address exception.
	badConn := h.dial(t)
	writeFull(t, badConn, readReq(0x2222, 0x01, 999, 1), 0)
	readExactFrame(t, badConn)
	_ = badConn.Close()

	var ar auditResponse
	getJSON(t, "http://"+h.control+"/audit?limit=10", &ar)
	if ar.Count < 2 {
		t.Fatalf("audit count = %d, want >=2", ar.Count)
	}
	var sawOK, sawIllegalAddr bool
	for _, r := range ar.Rows {
		if r.TxnID == 0x1111 && r.Exception == 0 && r.Category == "" {
			sawOK = true
		}
		if r.TxnID == 0x2222 && r.Exception == 0x02 &&
			r.Category == "illegal_data_address" {
			sawIllegalAddr = true
		}
	}
	if !sawOK {
		t.Errorf("normal request not audited correctly: %s", h.auditJSON(t))
	}
	if !sawIllegalAddr {
		t.Errorf("illegal-address request not audited with category: %s",
			h.auditJSON(t))
	}

	var stats struct {
		RequestsOK    float64            `json:"requests_ok"`
		FramingErrors float64            `json:"framing_errors"`
		Exceptions    map[string]float64 `json:"exceptions"`
	}
	getJSON(t, "http://"+h.control+"/stats", &stats)
	if stats.RequestsOK < 1 {
		t.Errorf("requests_ok = %v, want >=1", stats.RequestsOK)
	}
	if stats.Exceptions["0x02"] < 1 {
		t.Errorf("exception 0x02 counter missing: %v", stats.Exceptions)
	}
}

// TestControlPlaneRegistersSnapshot reads back the seeded register values
// and verifies the 404 for an unbound unit.
func TestControlPlaneRegistersSnapshot(t *testing.T) {
	h := startHarness(t, configLatencyNone, 0)
	defer h.shutdown()

	var resp struct {
		Unit          int      `json:"unit"`
		RegisterCount int      `json:"register_count"`
		ValuesHex     []string `json:"values_hex"`
	}
	getJSON(t, "http://"+h.control+"/registers?unit=1", &resp)
	if resp.RegisterCount != 125 {
		t.Fatalf("register_count = %d", resp.RegisterCount)
	}
	if len(resp.ValuesHex) != 125 || resp.ValuesHex[0] != "4000" {
		t.Fatalf("unexpected values: len=%d first=%v",
			len(resp.ValuesHex), firstOrEmpty(resp.ValuesHex))
	}

	// Unbound unit -> 404.
	r, err := http.Get("http://" + h.control + "/registers?unit=42")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	defer r.Body.Close()
	if r.StatusCode != http.StatusNotFound {
		t.Fatalf("unbound unit status = %d, want 404", r.StatusCode)
	}

	// Bad unit parameter -> 400.
	r2, err := http.Get("http://" + h.control + "/registers?unit=abc")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	defer r2.Body.Close()
	if r2.StatusCode != http.StatusBadRequest {
		t.Fatalf("bad unit status = %d, want 400", r2.StatusCode)
	}
}

func firstOrEmpty(s []string) string {
	if len(s) == 0 {
		return ""
	}
	return s[0]
}

// readReq builds a raw FC03 request frame (independently via vectors).
func readReq(txn uint16, unit byte, addr, qty uint16) []byte {
	return frameBytes(txn, uint16(unit),
		0x03, byte(addr>>8), byte(addr), byte(qty>>8), byte(qty))
}

func frameBytes(txn, unit uint16, pdu ...byte) []byte {
	length := uint16(1 + len(pdu))
	out := make([]byte, 0, 7+len(pdu))
	out = append(out,
		byte(txn>>8), byte(txn),
		0x00, 0x00,
		byte(length>>8), byte(length),
		byte(unit))
	return append(out, pdu...)
}

func getJSON(t *testing.T, url string, into any) {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET %s status = %d", url, resp.StatusCode)
	}
	if into != nil {
		if err := json.NewDecoder(resp.Body).Decode(into); err != nil {
			t.Fatalf("decode %s: %v", url, err)
		}
	}
}
