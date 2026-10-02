// Package cross_test contains language-interoperability tests: the Go client
// talks over real UDP to the independent Python STUN oracle
// (test/oracle/stun_oracle.py). Every response the Go client accepts or
// rejects was produced by a separately written implementation, so these tests
// cannot pass by both sides sharing a bug.
package cross_test

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"stunlab/internal/client"
	"stunlab/internal/server"
	"stunlab/internal/stun"
)

// oraclePath locates the python oracle relative to the repo root regardless of
// the test's working directory.
func oraclePath(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	root := wd
	for {
		cand := filepath.Join(root, "test", "oracle", "stun_oracle.py")
		if _, err := os.Stat(cand); err == nil {
			return cand
		}
		parent := filepath.Dir(root)
		if parent == root {
			t.Skip("oracle not found; run from repo")
		}
		root = parent
	}
}

// startOracle launches `python3 stun_oracle.py reply` and returns its address.
func startOracle(t *testing.T, mode, key string) (string, func()) {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	// Port 0 lets the oracle bind an ephemeral port; it prints the chosen
	// address on stdout once bound.
	cmd := exec.Command("python3", oraclePath(t), "reply",
		"--mode", mode, "--port", "0", "--key", key)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Skipf("cannot start python oracle: %v", err)
	}
	addr := ""
	sc := bufio.NewScanner(stdout)
	if sc.Scan() {
		line := strings.TrimSpace(sc.Text()) // "oracle listening on 127.0.0.1:PORT"
		if i := strings.LastIndex(line, ":"); i > 0 {
			addr = "127.0.0.1:" + line[i+1:]
		}
	}
	if _, pErr := strconv.Atoi(strings.TrimPrefix(addr, "127.0.0.1:")); pErr != nil {
		_ = cmd.Process.Kill()
		t.Fatalf("could not parse oracle address from stdout %q; stderr=%s",
			lineOrEmpty(sc), stderr.String())
	}
	return addr, func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}
}

func lineOrEmpty(sc *bufio.Scanner) string {
	if sc != nil {
		return sc.Text()
	}
	return ""
}

func bindWith(t *testing.T, addr, key string, timeout time.Duration) (*client.Result, error) {
	t.Helper()
	cl, err := client.Dial(context.Background(), client.Config{
		Key: []byte(key), Timeout: timeout, MaxAttempts: 2, Retransmit: 100 * time.Millisecond,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cl.Close() })
	return cl.Bind(context.Background(), addr)
}

func TestGoClientAcceptsPythonOracleSuccess(t *testing.T) {
	addr, stop := startOracle(t, "reflect", "labkey")
	defer stop()
	res, err := bindWith(t, addr, "labkey", time.Second)
	if err != nil {
		t.Fatalf("Go client rejected independent Python success response: %v", err)
	}
	if res.ErrorCode != 0 || res.Endpoint.Port == 0 {
		t.Fatalf("bad result: %+v", res)
	}
	if res.Endpoint.IP.String() != "127.0.0.1" {
		t.Fatalf("oracle reflected %s, want 127.0.0.1", res.Endpoint.IP)
	}
	// The HMAC the Go client verified was computed entirely in Python.
	if res.Attempts != 1 {
		t.Fatalf("attempts=%d: retransmission suggests a demux problem", res.Attempts)
	}
}

func TestGoClientRejectsPythonOracleWrongTxn(t *testing.T) {
	addr, stop := startOracle(t, "badtxn", "labkey")
	defer stop()
	_, err := bindWith(t, addr, "labkey", 500*time.Millisecond)
	if stun.ErrorOf(err) != stun.KindTimeout {
		t.Fatalf("wrong-txn oracle response accepted: %v", err)
	}
}

func TestGoClientRejectsPythonOracleBadSource(t *testing.T) {
	addr, stop := startOracle(t, "badsrc", "labkey")
	defer stop()
	_, err := bindWith(t, addr, "labkey", 500*time.Millisecond)
	if stun.ErrorOf(err) != stun.KindTimeout {
		t.Fatalf("foreign-source oracle response completed the request: %v", err)
	}
}

func TestGoClientRejectsPythonOracleTamperedTag(t *testing.T) {
	addr, stop := startOracle(t, "tamper", "labkey")
	defer stop()
	_, err := bindWith(t, addr, "labkey", 500*time.Millisecond)
	if stun.ErrorOf(err) != stun.KindIntegrity {
		t.Fatalf("tampered-MI oracle response kind = %v, want integrity_failure", err)
	}
}

func TestGoClientRejectsPythonOracleWhenKeysDisagree(t *testing.T) {
	addr, stop := startOracle(t, "reflect", "server-key")
	defer stop()
	_, err := bindWith(t, addr, "client-key", 500*time.Millisecond)
	// The oracle still replies 200 syntactically, but HMAC verification must
	// fail before the endpoint is trusted.
	if stun.ErrorOf(err) != stun.KindIntegrity {
		t.Fatalf("disagreeing keys: kind = %v, want integrity_failure", err)
	}
}

// runGoServer starts an in-process Go STUN server and returns its address.
func runGoServer(t *testing.T, network, key string) (string, func()) {
	t.Helper()
	addr := "127.0.0.1:0"
	if network == "udp6" {
		addr = "[::1]:0"
	}
	srv, err := server.Listen(server.Config{
		Network: network, Addr: addr, Key: []byte(key),
	})
	if err != nil {
		if network == "udp6" {
			t.Skipf("IPv6 unavailable: %v", err)
		}
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	go func() { _ = srv.Serve(ctx) }()
	host := "127.0.0.1"
	if network == "udp6" {
		host = "[::1]"
	}
	return host + ":" + strconv.Itoa(srv.LocalAddr().Port), func() {
		cancel()
		_ = srv.Close()
	}
}

// pythonBind runs the oracle as a client against a Go server and parses its
// single-line JSON verdict.
func pythonBind(t *testing.T, network, addr, key string, expectError int) map[string]any {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	args := []string{oraclePath(t), "bind", addr, "--net", network}
	if key != "" {
		args = append(args, "--key", key)
	}
	if expectError != 0 {
		args = append(args, "--expect-error", strconv.Itoa(expectError))
	}
	out, err := exec.Command("python3", args...).CombinedOutput()
	if err != nil && expectError == 0 {
		t.Fatalf("python bind failed: %v\n%s", err, out)
	}
	var verdict map[string]any
	if jerr := json.Unmarshal(bytes.TrimSpace(out), &verdict); jerr != nil {
		t.Fatalf("non-JSON oracle output: %v\n%s", jerr, out)
	}
	return verdict
}

func TestPythonClientAcceptsGoServerSuccess(t *testing.T) {
	for _, network := range []string{"udp4", "udp6"} {
		t.Run(network, func(t *testing.T) {
			addr, stop := runGoServer(t, network, "labkey")
			defer stop()
			v := pythonBind(t, network, addr, "labkey", 0)
			if ok, _ := v["ok"].(bool); !ok {
				t.Fatalf("python client rejected Go server: %v", v)
			}
			if v["endpoint"] == nil {
				t.Fatalf("missing reflected endpoint: %v", v)
			}
		})
	}
}

func TestPythonClientGetsGoServer420(t *testing.T) {
	addr, stop := runGoServer(t, "udp4", "")
	defer stop()
	v := pythonBind(t, "udp4", addr, "", 420)
	if ok, _ := v["ok"].(bool); !ok {
		t.Fatalf("420 path verdict not ok: %v", v)
	}
	if code, _ := v["error_code"].(float64); int(code) != 420 {
		t.Fatalf("error_code = %v, want 420", v["error_code"])
	}
	unk, _ := v["unknown_attributes"].([]any)
	if len(unk) != 1 || unk[0] != "0x0099" {
		t.Fatalf("unknown_attributes = %v, want [0x0099]", unk)
	}
}

func TestPythonClientRejectsUnsignedGoServerWhenExpectingIntegrity(t *testing.T) {
	// Go server runs with NO key: it ignores request MI and sends unsigned
	// responses. The python client expects integrity, so it must reject the
	// missing MESSAGE-INTEGRITY as integrity_failure.
	addr, stop := runGoServer(t, "udp4", "")
	defer stop()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available")
	}
	out, err := exec.Command("python3", oraclePath(t), "bind", addr,
		"--key", "client-expects-mi").CombinedOutput()
	if err == nil {
		t.Fatalf("python accepted unsigned response: %s", out)
	}
	var v map[string]any
	if jerr := json.Unmarshal(bytes.TrimSpace(out), &v); jerr != nil {
		t.Fatalf("non-JSON: %v\n%s", jerr, out)
	}
	if v["kind"] != "integrity_failure" {
		t.Fatalf("kind = %v, want integrity_failure", v["kind"])
	}
}
