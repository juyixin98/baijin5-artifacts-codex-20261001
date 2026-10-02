// Command bercli is an offline CLI front-end over the BER codec, useful for
// acceptance checks that do not need the running service.
package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"runtime"

	"berd/internal/ber"
	"berd/internal/version"
)

func usage() {
	fmt.Fprintf(os.Stderr, `bercli - BER codec CLI (version %s, %s)

usage:
  bercli decode <hex>            decode one BER TLV, print JSON tree
  bercli der <hex>               canonicalize BER to DER, print hex
  bercli verify-der <hex>        exit 0 iff input is canonical DER
  bercli encode '<spec-json>'    encode a JSON spec to DER hex
  bercli version                 print version info
`, version.Version, runtime.Version())
	os.Exit(2)
}

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	lim := ber.DefaultLimits()
	arg := func(i int) string {
		if len(os.Args) <= i {
			usage()
		}
		return os.Args[i]
	}
	switch os.Args[1] {
	case "decode":
		data := mustHex(arg(2))
		root, err := ber.DecodeAll(data, lim)
		must(err)
		must(ber.Validate(root, lim))
		out, _ := json.MarshalIndent(root.ToJSON(lim), "", "  ")
		fmt.Println(string(out))
	case "der":
		data := mustHex(arg(2))
		root, err := ber.DecodeAll(data, lim)
		must(err)
		der, err := ber.EncodeDER(root, lim)
		must(err)
		fmt.Println(hex.EncodeToString(der))
	case "verify-der":
		data := mustHex(arg(2))
		must(ber.VerifyDER(data, lim))
		fmt.Println("ok: canonical DER")
	case "encode":
		var spec ber.Spec
		if err := json.Unmarshal([]byte(arg(2)), &spec); err != nil {
			fmt.Fprintln(os.Stderr, "spec json:", err)
			os.Exit(2)
		}
		node, err := ber.Build(spec, lim)
		must(err)
		der, err := ber.EncodeDER(node, lim)
		must(err)
		fmt.Println(hex.EncodeToString(der))
	case "version":
		fmt.Printf("bercli %s (%s)\n", version.Version, runtime.Version())
	default:
		usage()
	}
}

func mustHex(s string) []byte {
	b, err := hex.DecodeString(s)
	if err != nil {
		fmt.Fprintln(os.Stderr, "hex:", err)
		os.Exit(2)
	}
	return b
}

func must(err *ber.Error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
