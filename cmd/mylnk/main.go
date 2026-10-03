// Command mylnk is the CLI for the synthetic MKOB object linker.
//
// Subcommands:
//
//	mylnk asm <in.asm> <out.mkobj>
//	mylnk link -entry main [-map] a.mkobj b.mkobj ...
//	mylnk run  -entry main [-maxsteps N] a.mkobj ...
//	mylnk diff -asmdir <dir> -spec <file> [-objdir <dir>]
//	mylnk version
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"mylnk/internal/config"
	"mylnk/internal/logx"
	"mylnk/internal/objfmt"
	"mylnk/internal/pipeline"
	"mylnk/internal/semdiff"
)

// Version is the fixed toolchain version reported in logs.
const Version = "mylnk 1.0.0 (MKOB v1, go1.22)"

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "asm":
		err = cmdAsm(os.Args[2:])
	case "link":
		err = cmdLink(os.Args[2:], false)
	case "run":
		err = cmdRun(os.Args[2:])
	case "diff":
		err = cmdDiff(os.Args[2:])
	case "version":
		fmt.Println(Version)
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "mylnk: "+err.Error())
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: mylnk {asm|link|run|diff|version} [args]")
}

func cmdAsm(args []string) error {
	if len(args) != 2 {
		return fmt.Errorf("usage: mylnk asm <in.asm> <out.mkobj>")
	}
	o, err := pipeline.AssembleFile(args[0])
	if err != nil {
		return err
	}
	b, err := objfmt.Encode(o)
	if err != nil {
		return err
	}
	return os.WriteFile(args[1], b, 0o644)
}

func cmdLink(args []string, run bool) error {
	fs := flag.NewFlagSet("link", flag.ContinueOnError)
	entry := fs.String("entry", "main", "entry symbol")
	printMap := fs.Bool("map", false, "print section map and GC decisions")
	fs.Parse(args)
	paths := fs.Args()
	if len(paths) == 0 {
		return fmt.Errorf("usage: mylnk link [-entry sym] [-map] <objects...>")
	}
	objs, err := loadObjs(paths)
	if err != nil {
		return err
	}
	li, undef, err := pipeline.LinkObjects(objs, *entry)
	if err != nil {
		return err
	}
	if len(undef) > 0 {
		for _, e := range undef {
			fmt.Fprintln(os.Stderr, e.Error())
		}
		return fmt.Errorf("link failed with %d undefined symbol(s)", len(undef))
	}
	if *printMap {
		printLinkMap(li)
	}
	return nil
}

func cmdRun(args []string) error {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "optional config file (entry/objects/maxsteps)")
	entry := fs.String("entry", "", "entry symbol (overrides config)")
	maxSteps := fs.Int("maxsteps", 0, "instruction step limit (overrides config)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg := config.Default()
	if *cfgPath != "" {
		cf, err := os.Open(*cfgPath)
		if err != nil {
			return err
		}
		cfg, err = config.Parse(cf)
		cf.Close()
		if err != nil {
			return err
		}
	}
	if *entry != "" {
		cfg.Entry = *entry
	}
	if *maxSteps > 0 {
		cfg.MaxSteps = *maxSteps
	}
	paths := fs.Args()
	if len(paths) == 0 && len(cfg.Objects) > 0 {
		paths = cfg.Objects
	}
	if len(paths) == 0 {
		return fmt.Errorf("usage: mylnk run [-entry sym] [-maxsteps N] <objects...>")
	}
	objs, err := loadObjs(paths)
	if err != nil {
		return err
	}
	li, undef, err := pipeline.LinkObjects(objs, cfg.Entry)
	if err != nil {
		return err
	}
	if len(undef) > 0 {
		for _, e := range undef {
			fmt.Fprintln(os.Stderr, e.Error())
		}
		return fmt.Errorf("link failed")
	}
	res, err := pipeline.Run(li, cfg.MaxSteps)
	if err != nil {
		return err
	}
	fmt.Printf("halt=%v steps=%d return=%d\n", res.Halted, res.Steps, res.Return)
	return nil
}

func cmdDiff(args []string) error {
	fs := flag.NewFlagSet("asm", flag.ContinueOnError)
	asmDir := fs.String("asmdir", "fixtures/asm", "directory with .asm fixtures")
	objDir := fs.String("objdir", "fixtures/gen", "directory with prebuilt .mkobj files")
	specPath := fs.String("spec", "fixtures/expectations.txt", "expectation table")
	useAsm := fs.Bool("from-asm", true, "assemble .asm fixtures on the fly")
	verbose := fs.Bool("v", true, "log steps")
	if err := fs.Parse(args); err != nil {
		return err
	}
	sf, err := os.Open(*specPath)
	if err != nil {
		return err
	}
	spec, err := semdiff.ParseSpec(sf)
	sf.Close()
	if err != nil {
		return err
	}
	loader := func(name string) (*objfmt.Object, error) {
		if *useAsm {
			base := strings.TrimSuffix(name, ".mkobj")
			return pipeline.AssembleFile(filepath.Join(*asmDir, base+".asm"))
		}
		return pipeline.LoadMkobj(filepath.Join(*objDir, name))
	}
	level := logx.LevelInfo
	if !*verbose {
		level = logx.LevelQuiet
	}
	log := logx.New(os.Stderr, runID(), level)
	log.Info("%s; spec cases=%d", Version, len(spec.Cases))
	failures := 0
	for _, c := range spec.Cases {
		rep := semdiff.RunCase(c, loader, log)
		if !rep.Passed {
			failures++
			log.Errorf("%s", rep.Mismatch.Error())
		}
	}
	if failures > 0 {
		return fmt.Errorf("semantic diff: %d/%d cases failed", failures, len(spec.Cases))
	}
	log.Verdict(true, "all %d differential cases passed", len(spec.Cases))
	return nil
}

func loadObjs(paths []string) ([]*objfmt.Object, error) {
	objs := make([]*objfmt.Object, 0, len(paths))
	for _, p := range paths {
		o, err := pipeline.LoadMkobj(p)
		if err != nil {
			return nil, fmt.Errorf("load %s: %w", p, err)
		}
		objs = append(objs, o)
	}
	return objs, nil
}

func printLinkMap(li *pipeline.LinkedImage) {
	fmt.Println("section                     vma       size  verdict")
	for _, ps := range li.Image.Sections {
		fmt.Printf("%-26s  %#08x  %-6d  KEPT\n",
			ps.Sec.Obj.Name+":"+ps.Sec.Name, ps.Addr, ps.Size)
	}
	for _, d := range li.Reach.Dropped {
		fmt.Printf("%-26s  %8s  %-6d  DROPPED\n",
			d.Obj.Name+":"+d.Name, "-", len(d.Native.Data))
	}
	fmt.Println("roots:")
	for _, r := range li.Reach.Roots {
		fmt.Printf("  %s:%s <- %s\n", r.Sec.Obj.Name, r.Sec.Name, r.Reason)
	}
}

func runID() string {
	if v := os.Getenv("MYLNK_RUN_ID"); v != "" {
		return v
	}
	return "run"
}
