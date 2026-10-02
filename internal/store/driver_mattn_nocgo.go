//go:build !cgo

package store

// Without cgo the mattn driver is unavailable; selecting it fails at
// config validation or Open. The constant keeps store.go compiling.
const mattnDriverName = "sqlite3-unavailable"
