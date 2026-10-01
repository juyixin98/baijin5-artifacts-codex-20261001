// Command ntpsim runs a configured local NTP synthesis simulation and reports
// per-round samples, uncertainty intervals and clock-selection decisions.
//
// It never modifies the system clock; the "client clock" is virtual time.
//
// Usage:
//
//	ntpsim -config configs/example.json [-db data/ntpsim.db] [-json]
//	ntpsim -query <run-id> -db data/ntpsim.db
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"ntpsim/internal/run"
)

func main() {
	if err := runCLI(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "ntpsim:", err)
		os.Exit(1)
	}
}

func runCLI(args []string) error {
	fs := flag.NewFlagSet("ntpsim", flag.ContinueOnError)
	cfgPath := fs.String("config", "", "path to scenario config JSON")
	dbPath := fs.String("db", "", "SQLite path to persist rounds (optional, e.g. data/ntpsim.db)")
	queryID := fs.String("query", "", "print persisted rounds for a run ID as JSON and exit")
	fs.Usage = func() {
		fmt.Fprintln(fs.Output(), "ntpsim - local synthetic NTP offset/RTT/clock-selection backend")
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		return err
	}

	if *queryID != "" {
		return queryRounds(*dbPath, *queryID)
	}
	if *cfgPath == "" {
		fs.Usage()
		return fmt.Errorf("-config is required")
	}

	opts := run.Options{ConfigPath: *cfgPath, Output: os.Stdout}
	if *dbPath != "" {
		st, err := run.OpenSQLite(*dbPath)
		if err != nil {
			return err
		}
		defer st.Close()
		opts.Store = st
	}
	sum, err := run.Execute(opts)
	if err != nil {
		return err
	}
	if *dbPath != "" {
		fmt.Fprintf(os.Stdout, "\nrun_id=%s persisted to %s\n", sum.RunID, *dbPath)
		fmt.Fprintf(os.Stdout, "inspect with: ntpsim -db %s -query %s\n", *dbPath, sum.RunID)
	}
	return nil
}

func queryRounds(dbPath, runID string) error {
	if dbPath == "" {
		return fmt.Errorf("-db is required with -query")
	}
	st, err := run.OpenSQLite(dbPath)
	if err != nil {
		return err
	}
	defer st.Close()
	rows, err := st.QueryRounds(runID)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	type outRow struct {
		Round    int    `json:"round"`
		Time     string `json:"virtual_time"`
		Status   string `json:"status"`
		Reason   string `json:"reason"`
		Selected string `json:"selected"`
		Offset   string `json:"offset"`
		Lower    string `json:"lower"`
		Upper    string `json:"upper"`
		Accepted int    `json:"accepted_sources"`
	}
	out := make([]outRow, 0, len(rows))
	for _, r := range rows {
		out = append(out, outRow{
			Round: r.Round, Time: r.VirtualTime, Status: r.Status, Reason: r.Reason,
			Selected: r.Selected, Offset: r.Offset.String(), Lower: r.Lower.String(),
			Upper: r.Upper.String(), Accepted: r.Accepted,
		})
	}
	return enc.Encode(out)
}
