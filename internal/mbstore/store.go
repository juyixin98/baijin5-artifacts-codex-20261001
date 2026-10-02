// Package mbstore is the register store of the slave fixture. It persists
// holding registers in SQLite (pure-Go driver, no cgo) and guarantees that
// a Write Multiple Registers request is applied atomically: either every
// register in the range is updated or none is.
package mbstore

import (
	"database/sql"
	"errors"
	"fmt"

	_ "modernc.org/sqlite" // pure-Go SQLite driver, registers "sqlite"
)

// ErrAddressRange is returned when a read or write touches an address
// outside the configured register space. The server maps it to the Modbus
// exception ILLEGAL_DATA_ADDRESS.
var ErrAddressRange = errors.New("register address out of configured range")

// Store holds the register space. All units share the same address space
// size; rows are pre-created so reads never depend on write history.
type Store struct {
	db   *sql.DB
	size uint16 // number of registers per unit, addresses 0..size-1
}

// Open opens (creating if necessary) the SQLite database at path and
// ensures the schema and the register rows for size addresses exist.
// Use ":memory:" for an ephemeral store.
func Open(path string, size uint16) (*Store, error) {
	if size == 0 {
		return nil, fmt.Errorf("register space size must be > 0")
	}
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// A single connection keeps ":memory:" databases stable and serializes
	// access; atomicity of multi-register writes comes from transactions (below).
	db.SetMaxOpenConns(1)
	s := &Store{db: db, size: size}
	if err := s.init(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) init() error {
	_, err := s.db.Exec(`CREATE TABLE IF NOT EXISTS registers (
		unit  INTEGER NOT NULL,
		addr  INTEGER NOT NULL,
		value INTEGER NOT NULL CHECK (value BETWEEN 0 AND 65535),
		PRIMARY KEY (unit, addr)
	)`)
	return err
}

// Size returns the number of registers per unit.
func (s *Store) Size() uint16 { return s.size }

// Close closes the underlying database.
func (s *Store) Close() error { return s.db.Close() }

// checkRange validates [addr, addr+qty) against the register space. The
// arithmetic is done in uint32 so addr+qty cannot wrap.
func (s *Store) checkRange(addr, qty uint16) error {
	if qty == 0 {
		return fmt.Errorf("%w: zero quantity", ErrAddressRange)
	}
	if uint32(addr)+uint32(qty) > uint32(s.size) {
		return fmt.Errorf("%w: [%d,%d) of %d", ErrAddressRange, addr, uint32(addr)+uint32(qty), s.size)
	}
	return nil
}

// EnsureUnit pre-creates zero-valued rows for the whole address space of a
// unit. It is idempotent.
func (s *Store) EnsureUnit(unit uint8) error {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stmt, err := tx.Prepare(`INSERT OR IGNORE INTO registers (unit, addr, value) VALUES (?, ?, 0)`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	for a := uint32(0); a < uint32(s.size); a++ {
		if _, err := stmt.Exec(int(unit), int(a)); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// Read returns qty consecutive registers starting at addr.
func (s *Store) Read(unit uint8, addr, qty uint16) ([]uint16, error) {
	if err := s.checkRange(addr, qty); err != nil {
		return nil, err
	}
	rows, err := s.db.Query(
		`SELECT addr, value FROM registers WHERE unit = ? AND addr >= ? AND addr < ? ORDER BY addr`,
		int(unit), int(addr), int(addr)+int(qty))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	values := make([]uint16, 0, qty)
	for rows.Next() {
		var a, v int
		if err := rows.Scan(&a, &v); err != nil {
			return nil, err
		}
		values = append(values, uint16(v))
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(values) != int(qty) {
		// Rows are pre-created by EnsureUnit; a short read means the unit
		// was never provisioned, which is an address-space problem too.
		return nil, fmt.Errorf("%w: unit %d not provisioned (got %d of %d rows)",
			ErrAddressRange, unit, len(values), qty)
	}
	return values, nil
}

// WriteMultiple atomically replaces the registers [addr, addr+len(values)).
// The whole update runs in one SQLite transaction; if any row is missing
// (unprovisioned unit) the transaction is rolled back and no register
// changes. Values are uint16 by construction, so the CHECK constraint can
// only fail on a programming error, which also rolls back.
func (s *Store) WriteMultiple(unit uint8, addr uint16, values []uint16) error {
	if err := s.checkRange(addr, uint16(len(values))); err != nil {
		return err
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stmt, err := tx.Prepare(`UPDATE registers SET value = ? WHERE unit = ? AND addr = ?`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	for i, v := range values {
		res, err := stmt.Exec(int(v), int(unit), int(addr)+i)
		if err != nil {
			return err
		}
		n, err := res.RowsAffected()
		if err != nil {
			return err
		}
		if n != 1 {
			return fmt.Errorf("%w: unit %d addr %d not provisioned",
				ErrAddressRange, unit, int(addr)+i)
		}
	}
	return tx.Commit()
}
