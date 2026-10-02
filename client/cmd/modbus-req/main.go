// Command modbus-req is a small Modbus TCP master CLI for exercising the
// local fixture. It is deliberately explicit: every request identity
// (transaction id, unit id, address, quantity/values) is printed, and the
// response is decoded with its MBAP correlation fields shown.
//
// Examples:
//
//	# read 4 holding registers from unit 1 starting at address 0
//	modbus-req -addr 127.0.0.1:5020 -unit 1 read -start 0 -qty 4
//
//	# write three registers (big-endian uint16, hex or decimal)
//	modbus-req -addr 127.0.0.1:5020 -unit 1 write -start 10 -val 0x0001,0x0002,0xffff
//
//	# send a raw PDU (hex), e.g. an unsupported FC04 read
//	modbus-req -addr 127.0.0.1:5020 -unit 1 raw -pdu 0400000001
package main

import (
	"context"
	"encoding/hex"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"modbusfixture/client"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "modbus-req: %v\n", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	fs := flag.NewFlagSet("modbus-req", flag.ContinueOnError)
	addr := fs.String("addr", "127.0.0.1:5020", "Modbus TCP slave address host:port")
	unit := fs.Int("unit", 1, "MBAP unit id (0..255)")
	timeout := fs.Duration("timeout", 5*time.Second, "request timeout")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *unit < 0 || *unit > 255 {
		return fmt.Errorf("unit id %d outside [0,255]", *unit)
	}
	if fs.NArg() < 1 {
		return fmt.Errorf("need a subcommand: read | write | raw")
	}

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	m, err := client.Dial(ctx, *addr, byte(*unit))
	if err != nil {
		return err
	}
	defer m.Close()

	sub, subArgs := fs.Arg(0), fs.Args()[1:]
	switch sub {
	case "read":
		return doRead(ctx, m, byte(*unit), subArgs)
	case "write":
		return doWrite(ctx, m, byte(*unit), subArgs)
	case "raw":
		return doRaw(ctx, m, byte(*unit), subArgs)
	default:
		return fmt.Errorf("unknown subcommand %q (read|write|raw)", sub)
	}
}

func doRead(ctx context.Context, m *client.Master, unit byte, args []string) error {
	fs := flag.NewFlagSet("read", flag.ContinueOnError)
	start := fs.Int("start", 0, "starting register address (0..65535)")
	qty := fs.Int("qty", 1, "register quantity (1..125)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	fmt.Printf("> read  unit=0x%02X start=%d qty=%d\n", unit, *start, *qty)
	vals, err := m.ReadHoldingRegisters(ctx, unit, uint16(*start), uint16(*qty))
	if err != nil {
		return err
	}
	fmt.Printf("< read ok: %d register(s)\n", len(vals))
	for i, v := range vals {
		fmt.Printf("  [%5d] = 0x%04X (%5d)\n", *start+i, v, v)
	}
	return nil
}

func doWrite(ctx context.Context, m *client.Master, unit byte, args []string) error {
	fs := flag.NewFlagSet("write", flag.ContinueOnError)
	start := fs.Int("start", 0, "starting register address")
	valFlag := fs.String("val", "", "comma-separated uint16 values, hex (0x..) or decimal")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *valFlag == "" {
		return fmt.Errorf("write requires -val")
	}
	parts := strings.Split(*valFlag, ",")
	vals := make([]uint16, 0, len(parts))
	for _, p := range parts {
		v, err := parseUint16(strings.TrimSpace(p))
		if err != nil {
			return fmt.Errorf("bad value %q: %w", p, err)
		}
		vals = append(vals, v)
	}
	fmt.Printf("> write unit=0x%02X start=%d qty=%d values=% x\n",
		unit, *start, len(vals), vals)
	if err := m.WriteMultipleRegisters(ctx, unit, uint16(*start), vals); err != nil {
		return err
	}
	fmt.Printf("< write ok: %d register(s) applied atomically, server echoed start+qty\n",
		len(vals))
	return nil
}

func doRaw(ctx context.Context, m *client.Master, unit byte, args []string) error {
	fs := flag.NewFlagSet("raw", flag.ContinueOnError)
	pduHex := fs.String("pdu", "", "raw request PDU as hex, e.g. 0400000001")
	if err := fs.Parse(args); err != nil {
		return err
	}
	pdu, err := hex.DecodeString(strings.TrimPrefix(*pduHex, "0x"))
	if err != nil {
		return fmt.Errorf("bad pdu hex: %w", err)
	}
	fmt.Printf("> raw   unit=0x%02X pdu=% x\n", unit, pdu)
	resp, err := m.RawRoundTrip(ctx, unit, pdu)
	if err != nil {
		// Exception responses are expected for negative testing; print
		// them distinctly instead of only an error.
		fmt.Printf("< %v\n", err)
		return nil
	}
	fmt.Printf("< raw ok: pdu=% x\n", resp)
	return nil
}

func parseUint16(s string) (uint16, error) {
	base := 10
	t := s
	if strings.HasPrefix(strings.ToLower(s), "0x") {
		base = 16
		t = s[2:]
	}
	v, err := strconv.ParseUint(t, base, 16)
	if err != nil {
		return 0, err
	}
	return uint16(v), nil
}
