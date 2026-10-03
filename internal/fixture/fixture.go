// Package fixture loads the synthetic, deterministic mail fixtures from
// disk (testdata/messages) into store.SeedMessage form. Fixtures are the
// only data source of the service; nothing here talks to a real mailbox.
package fixture

import (
	"os"
	"path/filepath"
	"sort"
	"strings"

	"imapd/internal/errs"
	"imapd/internal/store"
)

// Load reads every *.eml file in dir (sorted by name, which fixes the UID
// assignment order) and applies the initial flags listed in the optional
// flags.txt ("<file> <flag> <flag>...").
func Load(dir string) ([]store.SeedMessage, error) {
	names, err := filepath.Glob(filepath.Join(dir, "*.eml"))
	if err != nil {
		return nil, errs.Wrap(errs.CatInternal, "fixture.load", err, "bad pattern")
	}
	if len(names) == 0 {
		return nil, errs.New(errs.CatInput, "fixture.load", "no .eml fixtures in "+dir)
	}
	sort.Strings(names)
	flags, err := loadFlags(filepath.Join(dir, "flags.txt"))
	if err != nil {
		return nil, err
	}
	out := make([]store.SeedMessage, 0, len(names))
	for _, name := range names {
		content, err := os.ReadFile(name)
		if err != nil {
			return nil, errs.Wrap(errs.CatInternal, "fixture.load", err, "cannot read "+name)
		}
		out = append(out, store.SeedMessage{Content: content, Flags: flags[filepath.Base(name)]})
	}
	return out, nil
}

func loadFlags(path string) (map[string][]string, error) {
	out := make(map[string][]string)
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return out, nil
		}
		return nil, errs.Wrap(errs.CatInternal, "fixture.load", err, "cannot read flags file")
	}
	for i, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 2 {
			return nil, errs.New(errs.CatInput, "fixture.load",
				"flags.txt line "+itoa(i+1)+": expected '<file> <flag>...'")
		}
		out[fields[0]] = fields[1:]
	}
	return out, nil
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
