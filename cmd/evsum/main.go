// Command evsum prints the key incremental-build fields of a build report.
package main

import (
	"encoding/json"
	"fmt"
	"os"
)

type report struct {
	ChangedSymbols []struct {
		Key      string `json:"key"`
		Category string `json:"category"`
	} `json:"changed_symbols"`
	InvalidatedSet []string `json:"minimal_invalidated_set"`
	ReusedModules  []string `json:"reused_modules"`
	FullRebuild    bool     `json:"full_rebuild"`
	CacheVerdict   string   `json:"cache_verdict"`
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: evsum report.json")
		os.Exit(2)
	}
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	var r report
	if err := json.Unmarshal(data, &r); err != nil {
		panic(err)
	}
	fmt.Printf(" full=%v verdict=%s\n", r.FullRebuild, r.CacheVerdict)
	fmt.Print(" changed   :")
	for _, c := range r.ChangedSymbols {
		fmt.Printf(" %s(%s)", c.Key, c.Category)
	}
	fmt.Println()
	fmt.Printf(" invalidate: %v\n", r.InvalidatedSet)
	fmt.Printf(" reused    : %v\n", r.ReusedModules)
}
