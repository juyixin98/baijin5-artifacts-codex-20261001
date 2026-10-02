// Package mbcli implements the mbfixture command line: serve, read and
// write subcommands. It is separated from package main so the command
// surface is testable in-process.
package mbcli

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"strings"
	"time"

	"mbfixture/internal/mbclient"
	"mbfixture/internal/mbconfig"
	"mbfixture/internal/mblog"
	"mbfixture/internal/mbproto"
	"mbfixture/internal/mbserver"
	"mbfixture/internal/mbstore"
)

// Version is reported in the server startup log record.
const Version = "mbfixture-1.0.0"

// Run executes the command line in args (without the program name) and
// returns the process exit code. stdout carries machine-readable results,
// stderr carries logs and diagnostics.
func Run(args []string, stdout, stderr io.Writer) int {
	if len(args) < 1 {
		usage(stderr)
		return 2
	}
	var err error
	switch args[0] {
	case "serve":
		err = cmdServe(args[1:], stderr)
	case "read":
		err = cmdRead(args[1:], stdout, stderr)
	case "write":
		err = cmdWrite(args[1:], stdout, stderr)
	default:
		usage(stderr)
		return 2
	}
	if err != nil {
		fmt.Fprintf(stderr, `{"error":%q,"category":%q}`+"\n", err.Error(), categoryOf(err))
		return 1
	}
	return 0
}

func usage(w io.Writer) {
	fmt.Fprintf(w, `usage:
  mbfixture serve -config config.json
  mbfixture read  -addr 127.0.0.1:1502 -unit 1 -reg 0 -qty 4
  mbfixture write -addr 127.0.0.1:1502 -unit 1 -reg 2 -values 10,258,65535
`)
}

func cmdServe(args []string, stderr io.Writer) error {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	fs.SetOutput(stderr)
	cfgPath := fs.String("config", "config.json", "path to JSON config")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cfg, err := mbconfig.Load(*cfgPath)
	if err != nil {
		return err
	}
	logW := stderr
	if cfg.LogPath != "" && cfg.LogPath != "-" {
		f, err := openLog(cfg.LogPath)
		if err != nil {
			return err
		}
		defer f.Close()
		logW = f
	}
	logger := mblog.New(logW, "server")

	store, err := mbstore.Open(cfg.DBPath, cfg.RegisterCount)
	if err != nil {
		return err
	}
	defer store.Close()

	srv, err := mbserver.New(cfg.UnitIDs, store, logger)
	if err != nil {
		return err
	}
	ln, err := net.Listen("tcp", cfg.Listen)
	if err != nil {
		return fmt.Errorf("listen %s: %w", cfg.Listen, err)
	}
	unitList := make([]int, len(cfg.UnitIDs))
	for i, u := range cfg.UnitIDs {
		unitList[i] = int(u)
	}
	logger.Event("serve_start", "addr", ln.Addr().String(),
		"units", unitList, "register_count", cfg.RegisterCount,
		"db", cfg.DBPath, "version", Version)
	fmt.Fprintf(stderr, "serving on %s (units %v, %d registers/unit)\n",
		ln.Addr(), cfg.UnitIDs, cfg.RegisterCount)
	return srv.Serve(ln)
}

func dialFlags(fs *flag.FlagSet) (addr *string, unit *uint, timeout *time.Duration) {
	addr = fs.String("addr", "127.0.0.1:1502", "slave address host:port")
	unit = fs.Uint("unit", 1, "unit id")
	timeout = fs.Duration("timeout", 3*time.Second, "response timeout")
	return
}

func cmdRead(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("read", flag.ContinueOnError)
	fs.SetOutput(stderr)
	addr, unit, timeout := dialFlags(fs)
	reg := fs.Uint("reg", 0, "start register address")
	qty := fs.Uint("qty", 1, "quantity of registers")
	if err := fs.Parse(args); err != nil {
		return err
	}
	cli, err := mbclient.Dial(*addr, *timeout, mblog.New(stderr, "client"))
	if err != nil {
		return err
	}
	defer cli.Close()
	values, err := cli.ReadHoldingRegisters(uint8(*unit), uint16(*reg), uint16(*qty))
	if err != nil {
		return err
	}
	return printJSON(stdout, map[string]any{
		"ok": true, "op": "read", "unit": *unit, "addr": *reg,
		"qty": *qty, "values": values,
	})
}

func cmdWrite(args []string, stdout, stderr io.Writer) error {
	fs := flag.NewFlagSet("write", flag.ContinueOnError)
	fs.SetOutput(stderr)
	addr, unit, timeout := dialFlags(fs)
	reg := fs.Uint("reg", 0, "start register address")
	valuesArg := fs.String("values", "", "comma-separated register values 0..65535")
	if err := fs.Parse(args); err != nil {
		return err
	}
	values, err := parseValues(*valuesArg)
	if err != nil {
		return err
	}
	cli, err := mbclient.Dial(*addr, *timeout, mblog.New(stderr, "client"))
	if err != nil {
		return err
	}
	defer cli.Close()
	if err := cli.WriteMultipleRegisters(uint8(*unit), uint16(*reg), values); err != nil {
		return err
	}
	return printJSON(stdout, map[string]any{
		"ok": true, "op": "write", "unit": *unit, "addr": *reg,
		"qty": len(values), "values": values,
	})
}

// parseValues parses "v1,v2,..." enforcing the 16-bit register range.
func parseValues(s string) ([]uint16, error) {
	if s == "" {
		return nil, fmt.Errorf("-values is required")
	}
	parts := strings.Split(s, ",")
	values := make([]uint16, 0, len(parts))
	for _, p := range parts {
		n, err := strconv.ParseUint(strings.TrimSpace(p), 10, 16)
		if err != nil {
			return nil, fmt.Errorf("value %q out of 16-bit range: %w", p, err)
		}
		values = append(values, uint16(n))
	}
	return values, nil
}

func printJSON(w io.Writer, v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(w, string(b))
	return err
}

// openLog opens the server log file for appending.
func openLog(path string) (io.WriteCloser, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, fmt.Errorf("open log: %w", err)
	}
	return f, nil
}

// categoryOf names the failure category for the CLI result line.
func categoryOf(err error) string {
	var exc *mbproto.ExceptionError
	switch {
	case errors.As(err, &exc):
		return "modbus_exception:" + mbproto.ExceptionCodeName(exc.Code)
	case errors.Is(err, mbclient.ErrTimeout):
		return "timeout"
	case errors.Is(err, mbclient.ErrClosed):
		return "connection_closed"
	default:
		return "protocol_or_io"
	}
}
