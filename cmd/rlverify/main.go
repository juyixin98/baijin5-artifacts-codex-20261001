// Command rlverify compiles RL fixtures, runs them, fingerprints their public
// interface and computes minimal invalidation sets across two snapshots.
//
// Usage:
//
//	rlverify compile --dir testdata/... [--semver 1.2.0] [--schema fp-v1]
//	rlverify run     --dir testdata/... [--semver ...]
//	rlverify diff    --old testdata/A --new testdata/B [--semver-old ...] [--semver-new ...] [--diag]
//
// All inputs are local synthetic files; no network or accounts are used.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"rlmod/internal/config"
	"rlmod/internal/diag"
	"rlmod/internal/fingerprint"
	"rlmod/internal/pipeline"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "compile":
		err = cmdCompile(os.Args[2:])
	case "run":
		err = cmdRun(os.Args[2:])
	case "diff":
		err = cmdDiff(os.Args[2:])
	case "verify":
		err = cmdVerify(os.Args[2:])
	case "-h", "--help", "help":
		usage()
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "rlverify: "+err.Error())
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: rlverify {compile|run|diff} [flags]")
}

func cmdCompile(args []string) error {
	fs := flag.NewFlagSet("compile", flag.ContinueOnError)
	dir := fs.String("dir", "", "fixture directory")
	semver := fs.String("semver", "1.0.0", "compile semantic version")
	schema := fs.String("schema", fingerprint.SchemaVersion, "fingerprint schema")
	jsonOut := fs.Bool("json", false, "emit JSON manifest")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *dir == "" {
		return fmt.Errorf("--dir is required")
	}
	b, err := pipeline.Load(*dir, *semver, *schema)
	if err != nil {
		return err
	}
	if *jsonOut {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(b.Report)
	}
	fmt.Print(pipeline.RenderFingerprints(b))
	return nil
}

func cmdRun(args []string) error {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	dir := fs.String("dir", "", "fixture directory")
	semver := fs.String("semver", "1.0.0", "compile semantic version")
	schema := fs.String("schema", fingerprint.SchemaVersion, "fingerprint schema")
	fn := fs.String("fn", "", "function to call (default: entrypoint Run/Main)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *dir == "" {
		return fmt.Errorf("--dir is required")
	}
	b, err := pipeline.Load(*dir, *semver, *schema)
	if err != nil {
		return err
	}
	if *fn == "" {
		v, err := b.Run()
		if err != nil {
			return err
		}
		fmt.Println(v.Format())
		return nil
	}
	v, err := b.Call(*fn, nil)
	if err != nil {
		return err
	}
	fmt.Println(v.Format())
	return nil
}

func cmdVerify(args []string) error {
	fs := flag.NewFlagSet("verify", flag.ContinueOnError)
	cfgPath := fs.String("config", "config/rlverify.json", "config file")
	diagOn := fs.Bool("diag", false, "emit JSON-lines diagnostics to stderr")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := config.Load(*cfgPath)
	if err != nil {
		return err
	}
	results := pipeline.Verify(cfg, os.Stderr, *diagOn)
	failures := 0
	for _, r := range results {
		status := "PASS"
		if !r.OK {
			status = "FAIL"
			failures++
		}
		fmt.Printf("%s  %s  %s\n", status, r.Name, r.Detail)
	}
	if failures > 0 {
		return fmt.Errorf("%d scenario(s) failed", failures)
	}
	return nil
}

func cmdDiff(args []string) error {
	fs := flag.NewFlagSet("diff", flag.ContinueOnError)
	oldDir := fs.String("old", "", "old fixture directory")
	newDir := fs.String("new", "", "new fixture directory")
	oldSem := fs.String("semver-old", "1.0.0", "old compile semantic version")
	newSem := fs.String("semver-new", "1.0.0", "new compile semantic version")
	oldSchema := fs.String("schema-old", fingerprint.SchemaVersion, "old fingerprint schema")
	newSchema := fs.String("schema-new", fingerprint.SchemaVersion, "new fingerprint schema")
	useDiag := fs.Bool("diag", false, "also emit JSON-lines diagnostics to stderr")
	reqID := fs.String("request-id", "", "correlation id for diagnostics")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *oldDir == "" || *newDir == "" {
		return fmt.Errorf("--old and --new are required")
	}
	oldB, err := pipeline.Load(*oldDir, *oldSem, *oldSchema)
	if err != nil {
		return fmt.Errorf("old: %w", err)
	}
	newB, err := pipeline.Load(*newDir, *newSem, *newSchema)
	if err != nil {
		return fmt.Errorf("new: %w", err)
	}
	d := pipeline.Compare(oldB, newB)
	logger := diag.NewLogger(os.Stderr, *reqID, *useDiag)
	pipeline.EmitDiagnostics(logger, d)
	fmt.Print(pipeline.RenderDiff(d))
	return nil
}
