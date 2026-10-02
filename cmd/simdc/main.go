// Command simdc converts a scalar-condition loop request into restricted
// masked-SIMD IR, executes both the scalar reference and the SIMD runtime,
// and reports the per-channel semantic differential as JSON.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"simdc/internal/pipeline"
	"simdc/internal/transform"
)

func main() {
	reqPath := flag.String("request", "", "path to request JSON (default: stdin)")
	flag.Parse()

	var raw []byte
	var err error
	if *reqPath == "" || *reqPath == "-" {
		raw, err = io.ReadAll(os.Stdin)
	} else {
		raw, err = os.ReadFile(*reqPath)
	}
	if err != nil {
		fatal("IO_ERROR", err)
	}
	var req pipeline.Request
	if err := json.Unmarshal(raw, &req); err != nil {
		fatal("BAD_REQUEST_JSON", err)
	}
	resp := pipeline.Execute(req)
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(resp); err != nil {
		fatal("ENCODE_ERROR", err)
	}
	switch resp.Verdict {
	case transform.VerdictAccept:
		if resp.Diff != nil && !resp.Diff.Match {
			os.Exit(2)
		}
		os.Exit(0)
	case transform.VerdictUndetermined:
		os.Exit(3)
	default:
		os.Exit(1)
	}
}

func fatal(code string, err error) {
	fmt.Fprintf(os.Stderr, `{"error_code":%q,"message":%q}`+"\n", code, err.Error())
	os.Exit(4)
}
