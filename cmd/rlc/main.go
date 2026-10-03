// Command rlc is the reference driver for the restricted-language toolchain:
// build, run, fingerprint inspection and semantic diff.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"rlc/internal/build"
	"rlc/internal/config"
	"rlc/internal/diag"
	"rlc/internal/fp"
	"rlc/internal/frontend"
	"rlc/internal/interp"
	"rlc/internal/ir"
	"rlc/internal/lower"
	"rlc/internal/semdiff"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	cmd := os.Args[1]
	fs := flag.NewFlagSet(cmd, flag.ExitOnError)
	switch cmd {
	case "build":
		cfgPath := fs.String("config", "config/rlc.json", "project config")
		quiet := fs.Bool("quiet", false, "suppress diagnostics")
		_ = fs.Parse(os.Args[2:])
		os.Exit(runBuild(*cfgPath, *quiet))
	case "run":
		cfgPath := fs.String("config", "config/rlc.json", "project config")
		entry := fs.String("entry", "", "entry symbol module.fn")
		intArg := fs.Int("int", 0, "integer argument")
		_ = fs.Parse(os.Args[2:])
		os.Exit(runRun(*cfgPath, *entry, *intArg))
	case "fp":
		cfgPath := fs.String("config", "config/rlc.json", "project config")
		symbol := fs.String("symbol", "", "show one symbol")
		_ = fs.Parse(os.Args[2:])
		os.Exit(runFP(*cfgPath, *symbol))
	case "diff":
		before := fs.String("before", "", "fingerprint set JSON")
		after := fs.String("after", "", "fingerprint set JSON")
		_ = fs.Parse(os.Args[2:])
		os.Exit(runDiff(*before, *after))
	case "probe":
		cfgPath := fs.String("config", "config/rlc.json", "project config")
		fixtures := fs.String("fixtures", "testdata/probes.json", "probe fixture file")
		_ = fs.Parse(os.Args[2:])
		os.Exit(runProbes(*cfgPath, *fixtures))
	default:
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: rlc <build|run|fp|diff|probe> [flags]")
}

func load(cfgPath string, requestID string) (*config.Config, *build.Builder, *diag.Logger) {
	cfg, err := config.Load(cfgPath)
	must(err)
	log := diag.NewLogger(os.Stderr, requestID, "")
	return cfg, build.New(cfg, log), log
}

func runBuild(cfgPath string, quiet bool) int {
	log := diag.NewLogger(os.Stderr, diag.NewRequestID(), "")
	cfg, err := config.Load(cfgPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	if quiet {
		log = diag.NewLogger(nil, log.RequestID(), "")
	}
	b := build.New(cfg, log)
	rep, err := b.Build()
	if err != nil {
		fmt.Fprintln(os.Stderr, "build rejected:", err)
		return 1
	}
	out, _ := json.MarshalIndent(rep, "", "  ")
	fmt.Println(string(out))
	return 0
}

func runRun(cfgPath, entry string, intArg int) int {
	cfg, err := config.Load(cfgPath)
	must(err)
	log := diag.NewLogger(nil, "", "")
	b := build.New(cfg, log)
	rep, err := b.Build()
	if err != nil {
		fmt.Fprintln(os.Stderr, "build rejected:", err)
		return 1
	}
	target := entry
	if target == "" {
		target = cfg.EntryModule + "." + cfg.EntryFunc
	}
	eng := interp.New(rep.Program)
	v, err := eng.Call(target, []ir.Value{{Type: "int", I: int64(intArg)}})
	if err != nil {
		fmt.Fprintln(os.Stderr, "runtime error:", err)
		return 1
	}
	switch v.Type {
	case "int":
		fmt.Println(v.I)
	case "bool":
		fmt.Println(v.B)
	case "string":
		fmt.Println(v.S)
	default:
		fmt.Println("<void>")
	}
	return 0
}

func runFP(cfgPath, symbol string) int {
	cfg, err := config.Load(cfgPath)
	must(err)
	mods := map[string]*frontend.Module{}
	for _, m := range cfg.Modules {
		raw, err := os.ReadFile(cfg.SourceDir + "/" + m.File)
		must(err)
		ast, err := frontend.Parse(m.File, string(raw))
		must(err)
		mods[m.Name] = ast
	}
	an, err := lower.Analyze(mods, nil)
	must(err)
	set := fp.Compute(an)
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if symbol != "" {
		f, ok := set.Symbols[symbol]
		if !ok {
			fmt.Fprintln(os.Stderr, "no such symbol:", symbol)
			return 1
		}
		must(enc.Encode(f))
		return 0
	}
	must(enc.Encode(set))
	return 0
}

func runDiff(beforePath, afterPath string) int {
	if beforePath == "" || afterPath == "" {
		fmt.Fprintln(os.Stderr, "diff requires -before and -after")
		return 2
	}
	var before, after fp.FingerprintSet
	must(readJSON(beforePath, &before))
	must(readJSON(afterPath, &after))
	d := semdiff.Compare(&before, &after)
	out, _ := json.MarshalIndent(d, "", "  ")
	fmt.Println(string(out))
	return 0
}

func runProbes(cfgPath, fixturePath string) int {
	cfg, err := config.Load(cfgPath)
	must(err)
	log := diag.NewLogger(os.Stderr, diag.NewRequestID(), "")
	b := build.New(cfg, log)
	rep, err := b.Build()
	if err != nil {
		fmt.Fprintln(os.Stderr, "build rejected:", err)
		return 1
	}
	var probes struct {
		Probes []semdiff.Probe `json:"probes"`
	}
	must(readJSON(fixturePath, &probes))
	results := semdiff.RunProbes(rep.Program, probes.Probes, cfg.RedactLiterals)
	fail := 0
	for _, r := range results {
		status := "PASS"
		if !r.Pass {
			status = "FAIL"
			fail++
		}
		fmt.Printf("%s %s got=%s want=%s %s\n", status, r.Probe, r.GotRepr, r.WantRepr, r.Reason)
	}
	if fail > 0 {
		return 1
	}
	return 0
}

func readJSON(path string, v any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, v)
}

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
