// pmd is a small CLI for the pattern-match pipeline. It parses, compiles,
// matches and diffs programs locally, and can emit HTTP request bodies
// for the demo script.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"pmd/diff"
	"pmd/frontend"
	"pmd/ir"
	"pmd/runtime"
)

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "pmd: "+format+"\n", args...)
	os.Exit(1)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: pmd <compile|match|diff|req> [flags]")
		os.Exit(2)
	}
	cmd := os.Args[1]
	fs := flag.NewFlagSet(cmd, flag.ExitOnError)
	progPath := fs.String("p", "", "path to .pmd program file")
	valueJSON := fs.String("v", "", "value JSON, e.g. {\"ctor\":\"Nil\"}")
	cases := fs.Int("n", 300, "random cases for diff")
	seed := fs.Int64("seed", 1, "random seed for diff")
	depth := fs.Int("depth", 4, "max value depth for diff")
	_ = fs.Parse(os.Args[2:])

	readProgram := func() *frontend.Program {
		if *progPath == "" {
			fatal("-p program file is required")
		}
		b, err := os.ReadFile(*progPath)
		if err != nil {
			fatal("read program: %v", err)
		}
		prog, err := frontend.Parse(string(b))
		if err != nil {
			fatal("%v", err)
		}
		return prog
	}
	emit := func(v any) {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(v); err != nil {
			fatal("encode: %v", err)
		}
	}

	switch cmd {
	case "compile":
		prog := readProgram()
		tr := ir.Compile(prog)
		emit(tr)
	case "match":
		prog := readProgram()
		if *valueJSON == "" {
			fatal("-v value JSON is required")
		}
		var val runtime.Value
		if err := json.Unmarshal([]byte(*valueJSON), &val); err != nil {
			fatal("invalid value JSON: %v", err)
		}
		tr := ir.Compile(prog)
		outcome := runtime.EvalTree(tr, &val)
		seq := runtime.EvalSequential(prog, &val)
		agree, reason := diff.CompareOutcomes(outcome, seq)
		emit(map[string]any{
			"outcome": outcome,
			"verification": map[string]any{
				"reference_engine": "sequential",
				"agree":            agree,
				"reason":           reason,
			},
			"warnings": tr.Warnings,
		})
	case "diff":
		prog := readProgram()
		values := diff.Enumerate(prog.Ctors, prog.CtorOrder, 3, 2000)
		values = append(values, diff.Random(prog.Ctors, prog.CtorOrder, *seed, *cases, *depth)...)
		rep := diff.Run(prog, values)
		emit(rep)
		if !rep.OK() {
			os.Exit(1)
		}
	case "req":
		if *progPath == "" {
			fatal("-p program file is required")
		}
		// req only packages the source text; it must also work for
		// intentionally invalid programs, so no parsing happens here.
		b, err := os.ReadFile(*progPath)
		if err != nil {
			fatal("read program: %v", err)
		}
		body := map[string]any{"source": string(b)}
		if *valueJSON != "" {
			var val json.RawMessage
			if err := json.Unmarshal([]byte(*valueJSON), &val); err != nil {
				fatal("invalid value JSON: %v", err)
			}
			body["value"] = val
		}
		if *cases > 0 {
			body["cases"] = *cases
		}
		emit(body)
	default:
		fmt.Fprintln(os.Stderr, "usage: pmd <compile|match|diff|req> [flags]")
		os.Exit(2)
	}
}
