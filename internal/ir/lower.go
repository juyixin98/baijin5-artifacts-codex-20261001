package ir

import (
	"sort"
	"strings"

	"rlmod/internal/frontend"
)

func (c *compiler) lowerAll() error {
	for _, m := range c.modOrder() {
		mod := c.mods[m]
		for _, n := range sortedFuncNames(mod.Funcs) {
			if mod.Funcs[n].Generic {
				continue
			}
			decl := c.funcDecl(m, n)
			if err := c.lowerBody(m, mod.Funcs[n], nil, decl); err != nil {
				return err
			}
		}
	}
	// create and lower generic instances until a fixed point is reached
	for {
		progressed := false
		for _, m := range c.modOrder() {
			for _, f := range c.mods[m].Funcs {
				for _, in := range f.Instrs {
					if in.Op != OCall || in.Instance == "" {
						continue
					}
					gk := globalKey(in.Module, in.Str, in.Instance)
					if c.done[gk] {
						continue
					}
					if err := c.instantiate(in.Module, in.Str, in.Instance); err != nil {
						return err
					}
					c.done[gk] = true
					progressed = true
				}
			}
		}
		if !progressed {
			break
		}
	}
	return nil
}

func globalKey(mod, name, inst string) string {
	return mod + "|" + name + "|" + inst
}

func sortedFuncNames(fs map[string]*Func) []string {
	out := make([]string, 0, len(fs))
	for k := range fs {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedGenericNames(gs map[string]*GenericSource) []string {
	out := make([]string, 0, len(gs))
	for k := range gs {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func (c *compiler) funcDecl(m, n string) *frontend.FuncDecl {
	for _, cl := range c.parsed[m].Clauses {
		if cl.Func != nil && cl.Func.Name == n && len(cl.Func.TypeParams) == 0 {
			return cl.Func
		}
	}
	return nil
}

func (c *compiler) instantiate(mod, name, inst string) error {
	src, f, bind, err := c.ensureInstance(mod, name, inst)
	if err != nil {
		return err
	}
	return c.lowerBody(mod, f, bind, src.Decl)
}

// ensureInstance creates the (placeholder) function for a generic
// instantiation; its signature is computed immediately so call sites can be
// type checked before the body is lowered.
func (c *compiler) ensureInstance(mod, name, inst string) (*GenericSource, *Func, map[string]TypeRef, error) {
	src := c.mods[mod].GenericSrc[name]
	if src == nil {
		return nil, nil, nil, c.err(mod, 0, 0, KindUnknownSymbol, "no generic named %s", name)
	}
	key := name + "[" + inst + "]"
	if f, ok := c.mods[mod].Funcs[key]; ok {
		return src, f, c.bindFromInst(mod, src, inst), nil
	}
	argNames := strings.Split(inst, ",")
	if len(argNames) != len(src.TypeParams) {
		return nil, nil, nil, c.err(mod, 0, 0, KindTypeArgCount, "%s expects %d type args, got %d", name, len(src.TypeParams), len(argNames))
	}
	bind, err := c.parseBindings(mod, src, argNames)
	if err != nil {
		return nil, nil, nil, err
	}
	f := &Func{Module: mod, OrigName: name, Key: key, Generic: true, Line: 0}
	c.mods[mod].Funcs[key] = f
	if err := c.fillSignature(f, bind, src.Decl); err != nil {
		return nil, nil, nil, err
	}
	return src, f, bind, nil
}

func (c *compiler) parseBindings(mod string, src *GenericSource, argNames []string) (map[string]TypeRef, error) {
	bind := map[string]TypeRef{}
	for i, raw := range argNames {
		an := strings.TrimSpace(raw)
		parts := strings.SplitN(an, "/", 2)
		tr := TypeRef{Name: an}
		if len(parts) == 2 {
			tr = TypeRef{Module: parts[0], Name: parts[1]}
		}
		rt, err := c.resolveType(mod, frontend.TypeRef{Module: tr.Module, Name: tr.Name})
		if err != nil {
			return nil, err
		}
		bind[src.TypeParams[i]] = rt
	}
	return bind, nil
}

func (c *compiler) bindFromInst(mod string, src *GenericSource, inst string) map[string]TypeRef {
	bind, _ := c.parseBindings(mod, src, strings.Split(inst, ","))
	return bind
}

// fillSignature populates Params/Result of a (possibly generic) function
// using the supplied type-parameter binding.
func (c *compiler) fillSignature(f *Func, bind map[string]TypeRef, decl *frontend.FuncDecl) error {
	sub := func(t TypeRef) TypeRef {
		if bind != nil && t.Module == "" {
			if b, ok := bind[t.Name]; ok {
				return b
			}
		}
		return t
	}
	f.Params = make([]Param, 0, len(decl.Params))
	for _, p := range decl.Params {
		rt, err := c.resolveType(f.Module, p.Type)
		if err != nil {
			return err
		}
		f.Params = append(f.Params, Param{Name: p.Name, Type: sub(rt)})
	}
	if decl.Result.Name != "" {
		rt, err := c.resolveType(f.Module, decl.Result)
		if err != nil {
			return err
		}
		f.HasResult = true
		f.Result = sub(rt)
	}
	return nil
}
