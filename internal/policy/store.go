// Package policy owns the durable security state of the proxy, stored in
// SQLite: the host/port whitelist, username/password verifiers and the
// append-only request log. Nothing else in the system makes allow/deny
// decisions or persists security-relevant events.
//
// # Whitelist model
//
// Rules are one of two kinds:
//
//   - "cidr": an IP network (127.0.0.0/8, ::1/128, 10.0.0.0/8, ...). IP
//     literal requests, and every address a whitelisted domain resolves to,
//     must be covered by a cidr rule.
//   - "domain": an exact DNS label ("localhost") or a suffix wildcard
//     ("*.example.test" matches "a.example.test" but never "example.test"
//     and never "evilexample.test").
//
// A rule lists ports; the single port 0 means "any port".
// A domain request is allowed only when the name matches a domain rule AND
// every resolved address is covered by cidr rules. Binding both the name
// and the resolved addresses keeps the resolution result and the address
// policy consistent, closing the DNS-rebinding gap where a whitelisted name
// resolves to an out-of-scope address.
package policy

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/netip"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// RuleKind enumerates whitelist rule kinds.
type RuleKind string

const (
	KindCIDR   RuleKind = "cidr"
	KindDomain RuleKind = "domain"
)

// AnyPort in a rule's port list matches every port.
const AnyPort = 0

// Rule is one whitelist entry.
type Rule struct {
	Kind  RuleKind `json:"kind"`
	Host  string   `json:"host"`  // CIDR text, exact domain or "*.suffix"
	Ports []int    `json:"ports"` // [0] means any port
	Note  string   `json:"note,omitempty"`
}

// Decision explains a policy check for structured logging.
type Decision struct {
	Allowed bool
	Matched *Rule
	Reason  string // human-readable, populated also on denial
}

// Store is the SQLite-backed policy store. Safe for concurrent use.
type Store struct {
	db *sql.DB

	mu    sync.RWMutex
	rules []Rule
}

// Open opens (creating the schema if needed) the SQLite database at path.
// Use ":memory:" for ephemeral test databases.
func Open(ctx context.Context, path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("policy: open sqlite: %w", err)
	}
	// Single connection: modernc SQLite with shared in-memory/cache and our
	// own mutex-protected read model; avoids "database is locked" under
	// concurrent logging.
	db.SetMaxOpenConns(1)
	pragmas := []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA busy_timeout=5000",
		"PRAGMA foreign_keys=ON",
		"PRAGMA synchronous=NORMAL",
	}
	for _, p := range pragmas {
		if _, err := db.ExecContext(ctx, p); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("policy: %s: %w", p, err)
		}
	}
	s := &Store{db: db}
	if err := s.migrate(ctx); err != nil {
		_ = db.Close()
		return nil, err
	}
	if err := s.loadRules(ctx); err != nil {
		_ = db.Close()
		return nil, err
	}
	return s, nil
}

// DB exposes the underlying handle for read-only inspection (tests query
// the request log independently of the code under test).
func (s *Store) DB() *sql.DB { return s.db }

// Close releases the database.
func (s *Store) Close() error { return s.db.Close() }

func (s *Store) migrate(ctx context.Context) error {
	stmts := []string{
		`CREATE TABLE IF NOT EXISTS rules (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			kind TEXT NOT NULL CHECK (kind IN ('cidr','domain')),
			host TEXT NOT NULL,
			port INTEGER NOT NULL DEFAULT 0,
			note TEXT NOT NULL DEFAULT '',
			created_at TEXT NOT NULL,
			UNIQUE(kind, host, port)
		)`,
		`CREATE TABLE IF NOT EXISTS users (
			username TEXT PRIMARY KEY,
			salt BLOB NOT NULL,
			hash BLOB NOT NULL,
			iterations INTEGER NOT NULL,
			created_at TEXT NOT NULL,
			disabled INTEGER NOT NULL DEFAULT 0
		)`,
		`CREATE TABLE IF NOT EXISTS requests (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			request_id TEXT NOT NULL,
			started_at TEXT NOT NULL,
			finished_at TEXT,
			client_addr TEXT NOT NULL DEFAULT '',
			username TEXT NOT NULL DEFAULT '',
			stage TEXT NOT NULL DEFAULT '',
			target_host TEXT NOT NULL DEFAULT '',
			target_port INTEGER NOT NULL DEFAULT 0,
			resolved TEXT NOT NULL DEFAULT '',
			attempts TEXT NOT NULL DEFAULT '',
			outcome_kind TEXT NOT NULL DEFAULT '',
			detail TEXT NOT NULL DEFAULT '',
			bytes_up INTEGER NOT NULL DEFAULT 0,
			bytes_down INTEGER NOT NULL DEFAULT 0,
			duration_ms INTEGER NOT NULL DEFAULT 0
		)`,
		`CREATE INDEX IF NOT EXISTS idx_requests_reqid ON requests(request_id)`,
		`CREATE INDEX IF NOT EXISTS idx_requests_outcome ON requests(outcome_kind)`,
	}
	for _, st := range stmts {
		if _, err := s.db.ExecContext(ctx, st); err != nil {
			return fmt.Errorf("policy: migrate: %w", err)
		}
	}
	return nil
}

// ValidateRule normalizes and validates one rule, returning the canonical
// form. CIDRs are parsed; domains normalized to lower-case ASCII labels.
func ValidateRule(r Rule) (Rule, error) {
	out := Rule{Ports: append([]int(nil), r.Ports...), Note: r.Note}
	switch r.Kind {
	case KindCIDR:
		prefix, err := netip.ParsePrefix(strings.TrimSpace(r.Host))
		if err != nil {
			// Accept a bare IP as a host route (/32 or /128).
			addr, aerr := netip.ParseAddr(strings.TrimSpace(r.Host))
			if aerr != nil {
				return Rule{}, fmt.Errorf("rule %q: neither CIDR nor IP: %w", r.Host, err)
			}
			if addr.Is4() {
				prefix = netip.PrefixFrom(addr, 32)
			} else {
				prefix = netip.PrefixFrom(addr, 128)
			}
		}
		out.Kind = KindCIDR
		out.Host = prefix.Masked().String()
	case KindDomain:
		host := normalizeDomain(r.Host)
		// A wildcard rule "*.suffix" validates the suffix labels; the
		// wildcard itself is not a DNS label.
		toCheck := host
		if strings.HasPrefix(toCheck, "*.") {
			toCheck = strings.TrimPrefix(toCheck, "*.")
		}
		if err := validateDomain(toCheck); err != nil {
			return Rule{}, fmt.Errorf("rule %q: %w", r.Host, err)
		}
		out.Kind = KindDomain
		out.Host = host
	default:
		return Rule{}, fmt.Errorf("rule %q: unknown kind %q", r.Host, r.Kind)
	}
	if len(out.Ports) == 0 {
		out.Ports = []int{AnyPort}
	}
	for _, p := range out.Ports {
		if p < 0 || p > 65535 {
			return Rule{}, fmt.Errorf("rule %q: port %d out of range", r.Host, p)
		}
	}
	return out, nil
}

// ReplaceRules atomically replaces the full rule set, atomically refreshing
// the in-memory read model used on the hot path.
func (s *Store) ReplaceRules(ctx context.Context, rules []Rule) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("policy: begin: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	canonical := make([]Rule, 0, len(rules))
	seen := map[string]bool{}
	for _, r := range rules {
		cr, err := ValidateRule(r)
		if err != nil {
			return err
		}
		for _, p := range cr.Ports {
			key := fmt.Sprintf("%s|%s|%d", cr.Kind, cr.Host, p)
			if seen[key] {
				continue
			}
			seen[key] = true
			canonical = append(canonical, Rule{Kind: cr.Kind, Host: cr.Host, Ports: []int{p}, Note: cr.Note})
		}
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM rules`); err != nil {
		return fmt.Errorf("policy: clear rules: %w", err)
	}
	for _, r := range canonical {
		if _, err := tx.ExecContext(ctx,
			`INSERT INTO rules(kind, host, port, note, created_at) VALUES(?,?,?,?,?)`,
			string(r.Kind), r.Host, r.Ports[0], r.Note, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
			return fmt.Errorf("policy: reinsert rule: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("policy: commit: %w", err)
	}
	s.mu.Lock()
	s.rules = canonical
	s.mu.Unlock()
	return nil
}

func (s *Store) loadRules(ctx context.Context) error {
	rows, err := s.db.QueryContext(ctx, `SELECT kind, host, port, note FROM rules ORDER BY id`)
	if err != nil {
		return fmt.Errorf("policy: load rules: %w", err)
	}
	defer func() { _ = rows.Close() }()
	grouped := map[string]*Rule{}
	var order []string
	for rows.Next() {
		var kind, host, note string
		var port int
		if err := rows.Scan(&kind, &host, &port, &note); err != nil {
			return fmt.Errorf("policy: scan rule: %w", err)
		}
		key := kind + "|" + host
		if grouped[key] == nil {
			grouped[key] = &Rule{Kind: RuleKind(kind), Host: host, Note: note}
			order = append(order, key)
		}
		grouped[key].Ports = append(grouped[key].Ports, port)
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("policy: rules rows: %w", err)
	}
	rules := make([]Rule, 0, len(order))
	for _, k := range order {
		rules = append(rules, *grouped[k])
	}
	s.mu.Lock()
	s.rules = rules
	s.mu.Unlock()
	return nil
}

// snapshotRules returns the current rule set under read lock.
func (s *Store) snapshotRules() []Rule {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.rules
}

// CheckIP decides an IP literal request.
func (s *Store) CheckIP(addr netip.Addr, port int) Decision {
	if !addr.IsValid() {
		return Decision{Reason: "invalid IP address"}
	}
	addr = addr.Unmap()
	for _, r := range s.snapshotRules() {
		if r.Kind != KindCIDR {
			continue
		}
		prefix, err := netip.ParsePrefix(r.Host)
		if err != nil {
			continue
		}
		if prefix.Contains(addr) && portAllowed(r.Ports, port) {
			matched := r
			return Decision{Allowed: true, Matched: &matched, Reason: fmt.Sprintf("ip %s in %s port %d", addr, r.Host, port)}
		}
	}
	return Decision{Reason: fmt.Sprintf("ip %s port %d matched no cidr rule", addr, port)}
}

// CheckDomain decides a domain-name request against domain rules only.
// Resolved addresses must additionally be checked with CheckIPAll.
func (s *Store) CheckDomain(domain string, port int) Decision {
	name := normalizeDomain(domain)
	for _, r := range s.snapshotRules() {
		if r.Kind != KindDomain {
			continue
		}
		if domainMatches(r.Host, name) && portAllowed(r.Ports, port) {
			matched := r
			return Decision{Allowed: true, Matched: &matched, Reason: fmt.Sprintf("domain %q matches rule %q port %d", name, r.Host, port)}
		}
	}
	return Decision{Reason: fmt.Sprintf("domain %q port %d matched no domain rule", name, port)}
}

// CheckIPAll reports whether every address is covered by a cidr rule. On
// failure it returns the first uncovered address for the denial detail.
func (s *Store) CheckIPAll(addrs []netip.Addr, port int) (netip.Addr, bool) {
	for _, a := range addrs {
		if !s.CheckIP(a, port).Allowed {
			return a, false
		}
	}
	return netip.Addr{}, true
}

func portAllowed(rulePorts []int, port int) bool {
	for _, p := range rulePorts {
		if p == AnyPort || p == port {
			return true
		}
	}
	return false
}

// domainMatches implements exact and "*.suffix" semantics, label by label,
// so "*.example.test" never matches "example.test" or "evilexample.test".
func domainMatches(rule, name string) bool {
	if rule == name {
		return true
	}
	if strings.HasPrefix(rule, "*.") {
		suffix := rule[1:] // ".example.test"
		if strings.HasSuffix(name, suffix) {
			// Ensure there is at least one non-empty label before suffix.
			prefix := name[:len(name)-len(suffix)]
			return len(prefix) > 0 && !strings.ContainsAny(prefix, ".")
		}
	}
	return false
}

func normalizeDomain(h string) string {
	h = strings.TrimSpace(h)
	h = strings.ToLower(h)
	h = strings.TrimSuffix(h, ".")
	return h
}

// validateDomain enforces a conservative DNS-label syntax before the name
// ever reaches the resolver.
func validateDomain(h string) error {
	if h == "" || len(h) > 255 {
		return errors.New("domain length out of range")
	}
	labels := strings.Split(h, ".")
	for _, l := range labels {
		if l == "" || len(l) > 63 {
			return errors.New("empty or overlong DNS label")
		}
		for i, c := range l {
			switch {
			case c >= 'a' && c <= 'z', c >= '0' && c <= '9', c == '-':
			default:
				return fmt.Errorf("invalid character %q in label", c)
			}
			if (i == 0 || i == len(l)-1) && c == '-' {
				return errors.New("label cannot start or end with '-'")
			}
		}
	}
	return nil
}
