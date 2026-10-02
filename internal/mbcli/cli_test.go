package mbcli

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

// startServe runs the serve subcommand in-process on an ephemeral port and
// returns its announced address. The goroutine lives until the test
// process exits; the listener uses 127.0.0.1 only.
func startServe(t *testing.T) string {
	t.Helper()
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	cfg := `{"listen":"127.0.0.1:0","unit_ids":[1],"register_count":32,` +
		`"db_path":":memory:","log_path":"-","request_timeout_ms":2000}`
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o644); err != nil {
		t.Fatal(err)
	}
	pr, pw := io.Pipe()
	go Run([]string{"serve", "-config", cfgPath}, io.Discard, pw)

	addrCh := make(chan string, 1)
	go func() {
		re := regexp.MustCompile(`serving on (127\.0\.0\.1:\d+)`)
		buf := make([]byte, 4096)
		var acc string
		matched := false
		for {
			n, err := pr.Read(buf)
			if n > 0 && !matched {
				acc += string(buf[:n])
				if m := re.FindStringSubmatch(acc); m != nil {
					matched = true
					addrCh <- m[1]
				}
			}
			// Keep draining after the match: io.Pipe is synchronous and
			// the server's log writes would otherwise block forever.
			if err != nil {
				return
			}
		}
	}()
	select {
	case addr := <-addrCh:
		return addr
	case <-time.After(5 * time.Second):
		t.Fatal("server did not announce its address")
		return ""
	}
}

func TestRunWriteThenRead(t *testing.T) {
	addr := startServe(t)

	var out, errBuf bytes.Buffer
	if code := Run([]string{"write", "-addr", addr, "-unit", "1",
		"-reg", "5", "-values", "10,258,65535"}, &out, &errBuf); code != 0 {
		t.Fatalf("write exit=%d stderr=%s", code, errBuf.String())
	}
	out.Reset()
	if code := Run([]string{"read", "-addr", addr, "-unit", "1",
		"-reg", "5", "-qty", "3"}, &out, &errBuf); code != 0 {
		t.Fatalf("read exit=%d stderr=%s", code, errBuf.String())
	}
	var result struct {
		OK     bool     `json:"ok"`
		Values []uint16 `json:"values"`
	}
	if err := json.Unmarshal(out.Bytes(), &result); err != nil {
		t.Fatalf("read output not JSON: %q", out.String())
	}
	want := []uint16{10, 258, 65535}
	if !result.OK || len(result.Values) != 3 {
		t.Fatalf("unexpected result: %+v", result)
	}
	for i := range want {
		if result.Values[i] != want[i] {
			t.Fatalf("reg %d: got %d want %d", i, result.Values[i], want[i])
		}
	}
}

func TestRunFailureCategories(t *testing.T) {
	addr := startServe(t)
	var out, errBuf bytes.Buffer

	// Address out of range: 31+2 > 32 registers.
	if code := Run([]string{"read", "-addr", addr, "-unit", "1",
		"-reg", "31", "-qty", "2"}, &out, &errBuf); code != 1 {
		t.Fatalf("exit=%d, want 1", code)
	}
	if !strings.Contains(errBuf.String(), "modbus_exception:ILLEGAL_DATA_ADDRESS") {
		t.Fatalf("missing category in %q", errBuf.String())
	}

	// Value outside the 16-bit register range is rejected locally.
	errBuf.Reset()
	if code := Run([]string{"write", "-addr", addr, "-unit", "1",
		"-reg", "0", "-values", "65536"}, &out, &errBuf); code != 1 {
		t.Fatalf("exit=%d, want 1", code)
	}
	if !strings.Contains(errBuf.String(), "16-bit range") {
		t.Fatalf("missing range error in %q", errBuf.String())
	}

	// Unknown subcommand and missing args are usage errors (exit 2).
	if code := Run([]string{"frobnicate"}, &out, &errBuf); code != 2 {
		t.Fatalf("exit=%d, want 2", code)
	}
	if code := Run(nil, &out, &errBuf); code != 2 {
		t.Fatalf("exit=%d, want 2", code)
	}
}

func TestParseValues(t *testing.T) {
	v, err := parseValues("0, 1 ,65535")
	if err != nil || len(v) != 3 || v[2] != 65535 {
		t.Fatalf("got %v err %v", v, err)
	}
	if _, err := parseValues(""); err == nil {
		t.Fatal("empty values accepted")
	}
	if _, err := parseValues("1,-2"); err == nil {
		t.Fatal("negative value accepted")
	}
	if _, err := parseValues("70000"); err == nil {
		t.Fatal("out-of-range value accepted")
	}
}
