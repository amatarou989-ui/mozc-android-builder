#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from collections import Counter, defaultdict
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("patch_sudachi_dictionary.py")
SPEC = importlib.util.spec_from_file_location("patch_sudachi_dictionary", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("patch_sudachi_dictionary.pyを読み込めません")
MODULE = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_rule(
    action: str,
    *,
    surface: str = "元表記",
    reading: str = "もとよみ",
    new_surface: str = "",
    new_reading: str = "",
    allowed_readings: tuple[str, ...] = (),
    expected_matches: int = 1,
):
    return MODULE.Rule(
        index=1,
        action=action,
        surface=surface,
        reading=reading,
        new_surface=new_surface,
        new_reading=new_reading,
        allowed_readings=allowed_readings,
        expected_matches=expected_matches,
        note="test",
    )


def readings_from_pairs(pairs: Counter[tuple[str, str]]):
    result = defaultdict(set)
    for (surface, reading), count in pairs.items():
        if count > 0:
            result[surface].add(reading)
    return result


class RulePlanningTest(unittest.TestCase):
    def plan(self, rule, pairs):
        plans = MODULE.plan_rules([rule], pairs, readings_from_pairs(pairs))
        self.assertEqual(1, len(plans))
        return plans[0]

    def test_delete_missing_source_is_already_fixed(self):
        plan = self.plan(make_rule("DELETE"), Counter())
        self.assertEqual(MODULE.STATUS_ALREADY_FIXED, plan.status)

    def test_move_existing_target_is_already_fixed(self):
        rule = make_rule(
            "MOVE",
            new_surface="正表記",
            new_reading="ただしいよみ",
        )
        pairs = Counter({("正表記", "ただしいよみ"): 1})
        plan = self.plan(rule, pairs)
        self.assertEqual(MODULE.STATUS_ALREADY_FIXED, plan.status)

    def test_move_without_source_or_target_is_not_present(self):
        rule = make_rule(
            "MOVE",
            new_surface="正表記",
            new_reading="ただしいよみ",
        )
        plan = self.plan(rule, Counter())
        self.assertEqual(MODULE.STATUS_NOT_PRESENT, plan.status)

    def test_split_with_all_targets_is_already_fixed(self):
        rule = make_rule("SPLIT", new_reading="よみいち|よみに")
        pairs = Counter({
            ("元表記", "よみいち"): 1,
            ("元表記", "よみに"): 1,
        })
        plan = self.plan(rule, pairs)
        self.assertEqual(MODULE.STATUS_ALREADY_FIXED, plan.status)

    def test_keep_only_with_allowed_reading_is_already_fixed(self):
        rule = make_rule(
            "KEEP_ONLY",
            reading="",
            allowed_readings=("ただしいよみ",),
            expected_matches=3,
        )
        pairs = Counter({("元表記", "ただしいよみ"): 1})
        plan = self.plan(rule, pairs)
        self.assertEqual(MODULE.STATUS_ALREADY_FIXED, plan.status)

    def test_partially_fixed_rule_applies_remaining_source(self):
        rule = make_rule("DELETE", expected_matches=2)
        pairs = Counter({("元表記", "もとよみ"): 1})
        plan = self.plan(rule, pairs)
        self.assertEqual(MODULE.STATUS_APPLIED, plan.status)
        self.assertEqual(1, plan.source_before)

    def test_more_sources_than_baseline_is_unexpected(self):
        rule = make_rule("DELETE", expected_matches=1)
        pairs = Counter({("元表記", "もとよみ"): 2})
        plan = self.plan(rule, pairs)
        self.assertEqual(MODULE.STATUS_UNEXPECTED, plan.status)

    def test_keep_only_missing_correct_reading_is_not_present(self):
        rule = make_rule(
            "KEEP_ONLY",
            reading="",
            allowed_readings=("ただしいよみ",),
            expected_matches=2,
        )
        pairs = Counter({("元表記", "べつのよみ"): 1})
        plan = self.plan(rule, pairs)
        self.assertEqual(MODULE.STATUS_NOT_PRESENT, plan.status)


if __name__ == "__main__":
    unittest.main()
