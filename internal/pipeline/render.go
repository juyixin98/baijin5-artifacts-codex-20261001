package pipeline

import (
	"sort"
	"strings"

	"rlmod/internal/fingerprint"
	"rlmod/internal/ir"
	"rlmod/internal/semdiff"
)

// RenderFingerprints renders a deterministic text manifest.
func RenderFingerprints(b *Build) string {
	var sb strings.Builder
	sb.WriteString("schema " + b.Report.Schema + "\n")
	sb.WriteString("semver " + b.Report.SemVer + "\n")
	sb.WriteString("program " + b.Report.ProgramHash + "\n")
	for _, mname := range b.Order {
		mfp := b.Report.Modules[mname]
		sb.WriteString("module " + mname + " public=" + mfp.PublicHash + " full=" + mfp.FullHash + "\n")
		keys := make([]string, 0, len(mfp.Symbols))
		for k := range mfp.Symbols {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			sf := mfp.Symbols[k]
			vis := "private"
			if sf.Public {
				vis = "public"
			}
			line := "  " + sf.Kind + " " + vis + " " + sf.Name + " " + sf.Short
			d := sf.Details
			extra := []string{}
			if sf.Kind == "type" {
				extra = append(extra, "base="+d["base"])
			}
			if sf.Kind == "const" {
				val := d["inline_value"]
				if d["sensitive"] == "true" {
					val = maskInline(val)
				}
				extra = append(extra, "value="+val)
			}
			if sf.Kind == "func" || sf.Kind == "generic" {
				extra = append(extra, "sig=("+d["params"]+")->"+d["result"])
				if d["const_deps"] != "" {
					extra = append(extra, "const_deps="+d["const_deps"])
				}
				if d["call_deps"] != "" {
					extra = append(extra, "call_deps="+d["call_deps"])
				}
				if d["generic_deps"] != "" {
					extra = append(extra, "generic_deps="+d["generic_deps"])
				}
			}
			if len(extra) > 0 {
				line += "  " + strings.Join(extra, " ")
			}
			sb.WriteString(line + "\n")
		}
	}
	return sb.String()
}

func maskInline(v string) string {
	if i := strings.Index(v, ":"); i >= 0 {
		return v[:i+1] + "<redacted>"
	}
	return "<redacted>"
}

// RenderDiff renders the change classification and minimal invalidation set.
func RenderDiff(d *semdiff.Report) string {
	var sb strings.Builder
	sb.WriteString("schema " + d.OldSchema + " -> " + d.NewSchema + "\n")
	sb.WriteString("semver " + d.OldSemVer + " -> " + d.NewSemVer + "\n")
	if d.VersionRejected {
		sb.WriteString("version_gate INCONCLUSIVE " + d.VersionReason + "\n")
	} else {
		sb.WriteString("version_gate OK\n")
	}
	sb.WriteString("changes " + itoa(len(d.Changes)) + "\n")
	for _, c := range d.Changes {
		sb.WriteString("  " + string(c.Class) + " " + c.Module + "." + c.Name + " (" + c.Kind + ") -- " + c.Reason + "\n")
	}
	sb.WriteString("invalidated " + itoa(len(d.Invalidated)) + "\n")
	for _, u := range d.Invalidated {
		sb.WriteString("  X " + u + "\n")
	}
	sb.WriteString("reused " + itoa(len(d.ReuseAllowed)) + "\n")
	for _, u := range d.ReuseAllowed {
		sb.WriteString("  = " + u + "\n")
	}
	return sb.String()
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}

var _ = fingerprint.SchemaVersion
var _ = ir.PrimInt
