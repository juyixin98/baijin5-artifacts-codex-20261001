package ir

import "encoding/json"

type instrWire struct {
	Op    Op       `json:"op"`
	Int   int64    `json:"i,omitempty"`
	Str   string   `json:"s,omitempty"`
	Bool  bool     `json:"b,omitempty"`
	Slot  int      `json:"slot,omitempty"`
	Bin   BinOp    `json:"bin,omitempty"`
	Jump  int      `json:"jmp,omitempty"`
	Types []string `json:"types,omitempty"`
}

type funcWire struct {
	Name       string   `json:"name"`
	Module     string   `json:"module"`
	Short      string   `json:"short"`
	Params     []string `json:"params"`
	ParamTypes []string `json:"param_types"`
	Result     string   `json:"result"`
	Generic    bool     `json:"generic"`
	NumLocals  int      `json:"num_locals"`
	Body       []Instr  `json:"body"`
}

type constWire struct {
	Name   string `json:"name"`
	Module string `json:"module"`
	Short  string `json:"short"`
	Pub    bool   `json:"pub"`
	Type   string `json:"type"`
	Value  Value  `json:"value"`
}

type programWire struct {
	Modules []string    `json:"modules"`
	Funcs   []funcWire  `json:"funcs"`
	Consts  []constWire `json:"consts"`
}

func (i Instr) MarshalJSON() ([]byte, error) {
	return json.Marshal(instrWire{Op: i.Op, Int: i.Int, Str: i.Str, Bool: i.Bool, Slot: i.Slot, Bin: i.Bin, Jump: i.Jump, Types: i.Types})
}

func (i *Instr) UnmarshalJSON(data []byte) error {
	var w instrWire
	if err := json.Unmarshal(data, &w); err != nil {
		return err
	}
	*i = Instr{Op: w.Op, Int: w.Int, Str: w.Str, Bool: w.Bool, Slot: w.Slot, Bin: w.Bin, Jump: w.Jump, Types: w.Types}
	return nil
}

func MarshalProgram(p *Program) ([]byte, error) {
	w := programWire{Modules: p.Modules}
	for _, f := range p.Funcs {
		w.Funcs = append(w.Funcs, funcWire{
			Name: f.Name, Module: f.Module, Short: f.Short, Params: f.Params,
			ParamTypes: f.ParamTypes, Result: f.Result, Generic: f.Generic,
			NumLocals: f.NumLocals, Body: f.Body,
		})
	}
	for _, c := range p.Consts {
		w.Consts = append(w.Consts, constWire{Name: c.Name, Module: c.Module, Short: c.Short, Pub: c.Pub, Type: c.Type, Value: c.Value})
	}
	return json.MarshalIndent(&w, "", "  ")
}

func UnmarshalProgram(data []byte) (*Program, error) {
	var w programWire
	if err := json.Unmarshal(data, &w); err != nil {
		return nil, err
	}
	p := &Program{Modules: w.Modules}
	for _, fw := range w.Funcs {
		p.Funcs = append(p.Funcs, &Func{
			Name: fw.Name, Module: fw.Module, Short: fw.Short, Params: fw.Params,
			ParamTypes: fw.ParamTypes, Result: fw.Result, Generic: fw.Generic,
			NumLocals: fw.NumLocals, Body: fw.Body,
		})
	}
	for _, cw := range w.Consts {
		p.Consts = append(p.Consts, &Const{Name: cw.Name, Module: cw.Module, Short: cw.Short, Pub: cw.Pub, Type: cw.Type, Value: cw.Value})
	}
	return p, nil
}
