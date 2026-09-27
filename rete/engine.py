"""Engine: ties facts, network and agenda together; bounded execution.

Execution contract:
  * ``run(max_cycles)`` fires at most ``max_cycles`` activations and then
    stops. There is no unbounded auto-run anywhere in the engine.
  * The returned status is explicit: ``"quiescent"`` when the agenda drained,
    ``"cycle_limit_reached"`` when work remains. A limit stop is never
    reported as success.
  * Every fired activation and every fact insert/retract is written to the
    evidence store, correlated by ``run_id`` / ``session_id``.
"""

from __future__ import annotations

import itertools
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import __version__
from .agenda import Agenda
from .beta import delete_token
from .config import EngineConfig
from .errors import CycleLimitError, FactNotFoundError, RuleNotFoundError
from .facts import FactStore, WME
from .network import ReteNetwork
from .pattern import Rule, resolve, rule_from_dict

log = logging.getLogger("rete.engine")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FiredActivation:
    cycle: int
    rule: str
    salience: int
    fact_ids: tuple
    fact_keys: list
    bindings: dict
    action_results: list

    def to_dict(self) -> dict:
        return {
            "cycle": self.cycle,
            "rule": self.rule,
            "salience": self.salience,
            "fact_ids": list(self.fact_ids),
            "fact_keys": [list(k) for k in self.fact_keys],
            "bindings": dict(self.bindings),
            "action_results": list(self.action_results),
        }


@dataclass
class RunResult:
    run_id: str
    status: str                 # "quiescent" | "cycle_limit_reached"
    cycles: int
    max_cycles: int
    fired: list = field(default_factory=list)
    agenda_remaining: int = 0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "cycles": self.cycles,
            "max_cycles": self.max_cycles,
            "agenda_remaining": self.agenda_remaining,
            "fired": [f.to_dict() for f in self.fired],
        }


class Engine:
    def __init__(self, config: EngineConfig | None = None, store=None):
        self.config = config or EngineConfig()
        self.store = store
        self.session_id = uuid.uuid4().hex[:12]
        self.facts = FactStore()
        self.agenda = Agenda()
        self.network = ReteNetwork(self.agenda)
        self.outputs: list[dict] = []     # emit sink (observable side effects)
        self._run_counter = itertools.count(1)

    # -- rules ---------------------------------------------------------------
    def add_rule(self, rule: Rule | dict) -> str:
        if isinstance(rule, dict):
            rule = rule_from_dict(rule)
        self.network.add_rule(rule)
        return rule.name

    def rules(self) -> list[str]:
        return sorted(self.network.productions)

    # -- facts ---------------------------------------------------------------
    def insert(self, kind: str, fields: tuple,
               run_id: str | None = None) -> tuple[WME, bool]:
        """Insert one fact occurrence. Returns (wme, created).

        A duplicate identical fact bumps the refcount and returns
        created=False; it never produces a second set of matches."""
        wme, created = self.facts.insert(kind, fields)
        self._log_fact_event("insert", wme, run_id)
        if created:
            self.network.alpha.propagate_new_wme(wme)
            log.info("fact inserted session=%s run=%s wme_id=%d key=%s "
                     "matches_now=%d agenda=%d",
                     self.session_id, run_id or "-", wme.id, wme.key,
                     len(self.matches()), len(self.agenda))
        else:
            log.info("duplicate fact refcounted session=%s run=%s "
                     "wme_id=%d key=%s refcount=%d",
                     self.session_id, run_id or "-", wme.id, wme.key,
                     wme.count)
        return wme, created

    def retract(self, kind: str, fields: tuple, run_id: str | None = None) -> WME:
        """Retract one occurrence. Raises FactNotFoundError if absent.
        When the refcount reaches zero the WME and every dependent match
        and agenda activation are removed."""
        wme, removed = self.facts.retract(kind, fields)
        self._log_fact_event("retract", wme, run_id)
        if removed:
            for tok in list(wme.tokens):
                delete_token(tok)
            self.network.alpha.remove_wme(wme)
            log.info("fact retracted+removed session=%s run=%s wme_id=%d "
                     "key=%s agenda=%d", self.session_id, run_id or "-",
                     wme.id, wme.key, len(self.agenda))
        else:
            log.info("fact retracted refcount=%d session=%s run=%s "
                     "wme_id=%d key=%s", wme.count, self.session_id,
                     run_id or "-", wme.id, wme.key)
        return wme

    # -- matching ------------------------------------------------------------
    def matches(self, rule_name: str | None = None) -> list[dict]:
        """Current complete matches with provenance (fact ids + fact keys)."""
        names = [rule_name] if rule_name else self.rules()
        out = []
        for name in names:
            prod = self.network.productions.get(name)
            if prod is None:
                raise RuleNotFoundError(name)
            for tok in prod.tokens.values():
                out.append({
                    "rule": name,
                    "fact_ids": list(tok.wme_ids),
                    "fact_keys": [list(self._fact_key(i)) for i in tok.wme_ids],
                    "bindings": dict(tok.bindings),
                })
        out.sort(key=lambda m: (m["rule"], m["fact_ids"]))
        return out

    def _fact_key(self, wme_id: int) -> tuple:
        wme = self.facts.get(wme_id)
        return (wme.kind, *wme.fields) if wme else ("<retracted>", wme_id)

    # -- firing ----------------------------------------------------------------
    def run(self, max_cycles: int | None = None) -> RunResult:
        if max_cycles is None:
            max_cycles = self.config.default_max_cycles
        if not isinstance(max_cycles, int) or max_cycles < 1:
            raise CycleLimitError(f"max_cycles must be a positive integer, "
                                  f"got {max_cycles!r}")
        if max_cycles > self.config.max_cycles_limit:
            raise CycleLimitError(
                f"max_cycles {max_cycles} exceeds configured limit "
                f"{self.config.max_cycles_limit}")
        run_id = f"run-{next(self._run_counter)}-{uuid.uuid4().hex[:8]}"
        started = _utcnow()
        if self.store:
            self.store.start_run(run_id, started, max_cycles,
                                 engine_version=__version__,
                                 session_id=self.session_id)
        fired: list[FiredActivation] = []
        cycles = 0
        while self.agenda and cycles < max_cycles:
            act = self.agenda.pop()
            cycles += 1
            # Capture provenance BEFORE firing: a rule may legitimately
            # retract one of its own source facts; the activation record must
            # still name the facts that triggered it.
            fact_keys = [self._fact_key(i) for i in act.token.wme_ids]
            results = self._fire(act, run_id, cycles)
            rec = FiredActivation(
                cycle=cycles, rule=act.rule.name, salience=act.rule.salience,
                fact_ids=act.token.wme_ids, fact_keys=fact_keys,
                bindings=dict(act.token.bindings), action_results=results)
            fired.append(rec)
            log.info("activation fired session=%s run=%s cycle=%d rule=%s "
                     "salience=%d fact_ids=%s", self.session_id, run_id,
                     cycles, act.rule.name, act.rule.salience,
                     list(act.token.wme_ids))
            if self.store:
                self.store.record_activation(run_id, rec.to_dict())
        status = "quiescent" if not self.agenda else "cycle_limit_reached"
        result = RunResult(run_id=run_id, status=status, cycles=cycles,
                           max_cycles=max_cycles, fired=fired,
                           agenda_remaining=len(self.agenda))
        log.info("run finished session=%s run=%s status=%s cycles=%d/%d "
                 "agenda_remaining=%d", self.session_id, run_id, status,
                 cycles, max_cycles, len(self.agenda))
        if self.store:
            self.store.finish_run(run_id, _utcnow(), status, cycles)
        return result

    def _fire(self, act, run_id: str, cycle: int) -> list[dict]:
        results = []
        for action in act.rule.actions:
            fields = tuple(resolve(f, act.token.bindings)
                           for f in action.fields)
            if action.op == "assert":
                wme, created = self.insert(action.kind, fields, run_id=run_id)
                results.append({"op": "assert", "kind": action.kind,
                                "fields": list(fields), "wme_id": wme.id,
                                "outcome": ("asserted" if created
                                            else "deduped")})
            elif action.op == "retract":
                try:
                    wme = self.retract(action.kind, fields, run_id=run_id)
                    results.append({"op": "retract", "kind": action.kind,
                                    "fields": list(fields), "wme_id": wme.id,
                                    "outcome": "retracted"})
                except FactNotFoundError:
                    results.append({"op": "retract", "kind": action.kind,
                                    "fields": list(fields),
                                    "outcome": "fact_not_found"})
            else:  # emit
                record = {"tag": action.tag, "fields": list(fields),
                          "run_id": run_id, "cycle": cycle,
                          "rule": act.rule.name}
                self.outputs.append(record)
                results.append({"op": "emit", "tag": action.tag,
                                "fields": list(fields), "outcome": "emitted"})
        return results

    # -- evidence --------------------------------------------------------------
    def _log_fact_event(self, op: str, wme: WME, run_id: str | None) -> None:
        if self.store:
            self.store.record_fact_event(
                session_id=self.session_id, run_id=run_id, op=op,
                kind=wme.kind, fields=list(wme.fields), wme_id=wme.id,
                refcount=wme.count, ts=_utcnow())
