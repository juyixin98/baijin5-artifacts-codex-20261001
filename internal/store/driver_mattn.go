//go:build cgo

package store

import _ "github.com/mattn/go-sqlite3"

const mattnDriverName = "sqlite3"
