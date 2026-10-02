package compat

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

// TestCLIEndToEnd builds the real mbfixture binary, starts it as a slave,
// and drives it with its own read/write subcommands — the same commands
// documented in the README for local verification.
func TestCLIEndToEnd(t *testing.T) {
	bin := filepath.Join(t.TempDir(), "mbfixture")
	build := exec.Command("go", "build", "-o", bin, "mbfixture/cmd/mbfixture")
	build.Dir = ".."
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build CLI: %v\n%s", err, out)
	}

	cfgPath := filepath.Join(t.TempDir(), "config.json")
	cfg := `{"listen":"127.0.0.1:0","unit_ids":[1],"register_count":32,` +
		`"db_path":":memory:","log_path":"-","request_timeout_ms":2000}`
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o644); err != nil {
		t.Fatal(err)
	}

	serve := exec.Command(bin, "serve", "-config", cfgPath)
	stderr, err := serve.StderrPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := serve.Start(); err != nil {
		t.Fatal(err)
	}
	defer serve.Process.Kill()

	// The server announces its bound address on stderr:
	// "serving on 127.0.0.1:PORT (...)".
	addrCh := make(chan string, 1)
	go func() {
		re := regexp.MustCompile(`serving on (127\.0\.0\.1:\d+)`)
		buf := make([]byte, 4096)
		var acc string
		matched := false
		for {
			n, err := stderr.Read(buf)
			if n > 0 && !matched {
				acc += string(buf[:n])
				if m := re.FindStringSubmatch(acc); m != nil {
					matched = true
					addrCh <- m[1]
				}
			}
			// Keep draining after the match so the server's stderr never
			// fills the OS pipe and blocks its logging.
			if err != nil {
				return
			}
		}
	}()
	var addr string
	select {
	case addr = <-addrCh:
	case <-time.After(5 * time.Second):
		t.Fatal("server did not announce its address")
	}

	run := func(args ...string) (string, string, error) {
		cmd := exec.Command(bin, args...)
		var outBuf, errBuf strings.Builder
		cmd.Stdout, cmd.Stderr = &outBuf, &errBuf
		err := cmd.Run()
		return outBuf.String(), errBuf.String(), err
	}

	// Write 3 registers then read them back through the CLI.
	stdout, _, err := run("write", "-addr", addr, "-unit", "1",
		"-reg", "5", "-values", "10,258,65535")
	if err != nil {
		t.Fatalf("cli write: %v", err)
	}
	stdout, _, err = run("read", "-addr", addr, "-unit", "1", "-reg", "5", "-qty", "3")
	if err != nil {
		t.Fatalf("cli read: %v", err)
	}
	var result struct {
		OK     bool     `json:"ok"`
		Values []uint16 `json:"values"`
	}
	if err := json.Unmarshal([]byte(stdout), &result); err != nil {
		t.Fatalf("read output not JSON: %q", stdout)
	}
	want := []uint16{10, 258, 65535}
	if !result.OK || len(result.Values) != 3 {
		t.Fatalf("unexpected read result: %+v", result)
	}
	for i := range want {
		if result.Values[i] != want[i] {
			t.Fatalf("reg %d: got %d want %d", i, result.Values[i], want[i])
		}
	}

	// Out-of-range read must fail with a named exception category.
	_, stderrOut, err := run("read", "-addr", addr, "-unit", "1", "-reg", "31", "-qty", "2")
	if err == nil {
		t.Fatal("out-of-range read unexpectedly succeeded")
	}
	if !strings.Contains(stderrOut, "ILLEGAL_DATA_ADDRESS") {
		t.Fatalf("error output lacks failure category: %q", stderrOut)
	}
}
