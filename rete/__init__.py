"""Structured Rete production-rule engine."""

__version__ = "1.0.0"

from .engine import Engine, RunResult
from .errors import (CycleLimitError, DuplicateRuleError, FactNotFoundError,
                     ReteError, RuleError, RuleNotFoundError)
from .pattern import (Action, Condition, Rule, Test, is_var,
                      rule_from_dict)

__all__ = [
    "Engine", "RunResult",
    "ReteError", "RuleError", "RuleNotFoundError", "DuplicateRuleError",
    "FactNotFoundError", "CycleLimitError",
    "Rule", "Condition", "Test", "Action", "rule_from_dict", "is_var",
    "__version__",
]
