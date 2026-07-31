#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


STATUS_APPLIED = "APPLIED"
STATUS_ALREADY_FIXED = "ALREADY_FIXED"
STATUS_NOT_PRESENT = "NOT_PRESENT"
STATUS_UNEXPECTED = "UNEXPECTED"
BLOCKING_STATUSES = {STATUS_NOT_PRESENT, STATUS_UNEXPECTED}


@dataclass(frozen=True)
class Rule:
    index: int
    action: str
    surface: str
    reading: str
    new_surface: str
    new_reading: str
    allowed_readings: tuple[str, ...]
    expected_matches: int
    note: str


@dataclass(frozen=True)
class RulePlan:
    rule: Rule
    status: str
    source_before: int
    target_before: int
    missing_targets: tuple[str, ...]
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sudachi由来Mozc辞書へ、表記と読みの完全一致ルールを適用します。"
            "公式側で既に修正済みのルールはALREADY_FIXEDとして続行します。"
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    return parser.parse_args()


def read_rules(path: Path) -> list[Rule]:
    rules: list[Rule] = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        required = {
            "action",
            "surface",
            "reading",
            "new_surface",
            "new_reading",
            "allowed_readings",
            "expected_matches",
            "note",
        }
        if set(reader.fieldnames or []) != required:
            raise ValueError(
                f"補正表の列が不正です: actual={reader.fieldnames} "
                f"expected={sorted(required)}"
            )

        for index, row in enumerate(reader, start=1):
            action = row["action"].strip()
            if action not in {"DELETE", "MOVE", "SPLIT", "KEEP_ONLY"}:
                raise ValueError(f"{index}行目: 未対応action={action}")

            expected_matches = int(row["expected_matches"])
            if expected_matches < 1:
                raise ValueError(f"{index}行目: expected_matchesは1以上が必要です")

            allowed_readings = tuple(
                value for value in row["allowed_readings"].split("|") if value
            )
            rule = Rule(
                index=index,
                action=action,
                surface=row["surface"],
                reading=row["reading"],
                new_surface=row["new_surface"],
                new_reading=row["new_reading"],
                allowed_readings=allowed_readings,
                expected_matches=expected_matches,
                note=row["note"],
            )

            if not rule.surface:
                raise ValueError(f"{index}行目: surfaceが空です")
            if action in {"DELETE", "MOVE", "SPLIT"} and not rule.reading:
                raise ValueError(f"{index}行目: readingが空です")
            if action == "MOVE" and (not rule.new_surface or not rule.new_reading):
                raise ValueError(f"{index}行目: MOVEにはnew_surface/new_readingが必要です")
            if action == "SPLIT" and not rule.new_reading:
                raise ValueError(f"{index}行目: SPLITにはnew_readingが必要です")
            if action == "KEEP_ONLY" and not allowed_readings:
                raise ValueError(f"{index}行目: KEEP_ONLYにはallowed_readingsが必要です")

            rules.append(rule)

    source_keys: set[tuple[str, str]] = set()
    keep_only_surfaces: set[str] = set()
    for rule in rules:
        if rule.action == "KEEP_ONLY":
            if rule.surface in keep_only_surfaces:
                raise ValueError(f"KEEP_ONLYが重複しています: {rule.surface}")
            keep_only_surfaces.add(rule.surface)
            continue

        key = (rule.surface, rule.reading)
        if key in source_keys:
            raise ValueError(f"補正元が重複しています: {key}")
        source_keys.add(key)

    conflict_surfaces = keep_only_surfaces & {
        rule.surface for rule in rules if rule.action != "KEEP_ONLY"
    }
    if conflict_surfaces:
        raise ValueError(
            f"KEEP_ONLYと他ルールが競合しています: {sorted(conflict_surfaces)}"
        )

    return rules


def split_dictionary_line(
    line: str,
    line_number: int,
) -> tuple[str, str, str, str, str]:
    columns = line.rstrip("\n").split("\t")
    if len(columns) != 5:
        raise ValueError(
            f"辞書{line_number}行目の列数が5ではありません: {len(columns)}"
        )
    return tuple(columns)  # type: ignore[return-value]


def inspect_dictionary(
    path: Path,
) -> tuple[Counter[tuple[str, str]], dict[str, set[str]], int]:
    pair_counts: Counter[tuple[str, str]] = Counter()
    surface_readings: dict[str, set[str]] = defaultdict(set)
    line_count = 0

    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            reading, _left_id, _right_id, _cost, surface = split_dictionary_line(
                line,
                line_number,
            )
            pair_counts[(surface, reading)] += 1
            surface_readings[surface].add(reading)
            line_count += 1

    return pair_counts, surface_readings, line_count


def rule_targets(rule: Rule) -> tuple[tuple[str, str], ...]:
    if rule.action == "MOVE":
        return ((rule.new_surface, rule.new_reading),)
    if rule.action == "SPLIT":
        return tuple(
            (rule.surface, reading)
            for reading in rule.new_reading.split("|")
            if reading
        )
    if rule.action == "KEEP_ONLY":
        return tuple((rule.surface, reading) for reading in rule.allowed_readings)
    return ()


def plan_rules(
    rules: list[Rule],
    pair_counts: Counter[tuple[str, str]],
    surface_readings: dict[str, set[str]],
) -> list[RulePlan]:
    plans: list[RulePlan] = []

    for rule in rules:
        targets = rule_targets(rule)
        target_before = sum(pair_counts[target] for target in targets)
        missing_targets = tuple(
            f"{reading} -> {surface}"
            for surface, reading in targets
            if pair_counts[(surface, reading)] == 0
        )

        if rule.action == "KEEP_ONLY":
            existing = surface_readings.get(rule.surface, set())
            allowed = set(rule.allowed_readings)
            missing_allowed = allowed - existing
            source_before = sum(
                count
                for (surface, reading), count in pair_counts.items()
                if surface == rule.surface and reading not in allowed
            )

            if missing_allowed:
                plans.append(
                    RulePlan(
                        rule=rule,
                        status=STATUS_NOT_PRESENT,
                        source_before=source_before,
                        target_before=target_before,
                        missing_targets=tuple(sorted(missing_allowed)),
                        detail=(
                            "許可している正しい読みが辞書にありません。"
                            "公式側の変更を確認してください。"
                        ),
                    )
                )
            elif source_before > rule.expected_matches:
                plans.append(
                    RulePlan(
                        rule=rule,
                        status=STATUS_UNEXPECTED,
                        source_before=source_before,
                        target_before=target_before,
                        missing_targets=(),
                        detail=(
                            "削除対象の読みが基準時より増えています。"
                            "新しい読みを自動削除せず確認が必要です。"
                        ),
                    )
                )
            elif source_before == 0:
                plans.append(
                    RulePlan(
                        rule=rule,
                        status=STATUS_ALREADY_FIXED,
                        source_before=0,
                        target_before=target_before,
                        missing_targets=(),
                        detail="許可読みだけが残っており、公式側で修正済みです。",
                    )
                )
            else:
                plans.append(
                    RulePlan(
                        rule=rule,
                        status=STATUS_APPLIED,
                        source_before=source_before,
                        target_before=target_before,
                        missing_targets=(),
                        detail=(
                            "残っている非許可読みだけを削除します。"
                            if source_before < rule.expected_matches
                            else "基準時と同じ非許可読みを削除します。"
                        ),
                    )
                )
            continue

        source_before = pair_counts[(rule.surface, rule.reading)]
        if source_before > rule.expected_matches:
            plans.append(
                RulePlan(
                    rule=rule,
                    status=STATUS_UNEXPECTED,
                    source_before=source_before,
                    target_before=target_before,
                    missing_targets=missing_targets,
                    detail=(
                        "補正元が基準時より増えています。"
                        "重複または新しい辞書変更を確認してください。"
                    ),
                )
            )
            continue

        if source_before > 0:
            plans.append(
                RulePlan(
                    rule=rule,
                    status=STATUS_APPLIED,
                    source_before=source_before,
                    target_before=target_before,
                    missing_targets=missing_targets,
                    detail=(
                        "残っている補正元だけを処理します。"
                        if source_before < rule.expected_matches
                        else "基準時と同じ補正元を処理します。"
                    ),
                )
            )
            continue

        if rule.action == "DELETE":
            plans.append(
                RulePlan(
                    rule=rule,
                    status=STATUS_ALREADY_FIXED,
                    source_before=0,
                    target_before=0,
                    missing_targets=(),
                    detail="削除対象が既になく、公式側で修正済みです。",
                )
            )
        elif not missing_targets:
            plans.append(
                RulePlan(
                    rule=rule,
                    status=STATUS_ALREADY_FIXED,
                    source_before=0,
                    target_before=target_before,
                    missing_targets=(),
                    detail="補正元がなく、必要な補正先がすべて存在します。",
                )
            )
        else:
            plans.append(
                RulePlan(
                    rule=rule,
                    status=STATUS_NOT_PRESENT,
                    source_before=0,
                    target_before=target_before,
                    missing_targets=missing_targets,
                    detail=(
                        "補正元がなく、必要な補正先もそろっていません。"
                        "公式側の変更を確認してください。"
                    ),
                )
            )

    return plans


def report_target(rule: Rule) -> str:
    if rule.action == "MOVE":
        return f"{rule.new_reading} -> {rule.new_surface}"
    if rule.action == "SPLIT":
        return f"{rule.new_reading} -> {rule.surface}"
    if rule.action == "KEEP_ONLY":
        return "|".join(rule.allowed_readings)
    return ""


def make_report_rows(
    plans: list[RulePlan],
    matched: Counter[int] | None = None,
    removed: Counter[int] | None = None,
    added: Counter[int] | None = None,
    skipped_existing: Counter[int] | None = None,
) -> list[dict[str, str]]:
    matched = matched or Counter()
    removed = removed or Counter()
    added = added or Counter()
    skipped_existing = skipped_existing or Counter()

    rows: list[dict[str, str]] = []
    for plan in plans:
        rule = plan.rule
        rows.append(
            {
                "rule": str(rule.index),
                "status": plan.status,
                "action": rule.action,
                "surface": rule.surface,
                "reading": rule.reading,
                "target_or_allowed": report_target(rule),
                "baseline_expected_matches": str(rule.expected_matches),
                "source_before": str(plan.source_before),
                "target_before": str(plan.target_before),
                "missing_targets": "|".join(plan.missing_targets),
                "matched": str(matched[rule.index]),
                "removed": str(removed[rule.index]),
                "added": str(added[rule.index]),
                "skipped_existing": str(skipped_existing[rule.index]),
                "detail": plan.detail,
                "note": rule.note,
            }
        )
    return rows


REPORT_FIELDS = [
    "rule",
    "status",
    "action",
    "surface",
    "reading",
    "target_or_allowed",
    "baseline_expected_matches",
    "source_before",
    "target_before",
    "missing_targets",
    "matched",
    "removed",
    "added",
    "skipped_existing",
    "detail",
    "note",
]


def write_report(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=REPORT_FIELDS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_summary(
    path: Path,
    rows: list[dict[str, str]],
    input_lines: int,
    output_lines: int | None,
    blocked: bool,
) -> None:
    status_counts = Counter(row["status"] for row in rows)
    total_removed = sum(int(row["removed"]) for row in rows)
    total_added = sum(int(row["added"]) for row in rows)

    lines = [
        f"result={'BLOCKED' if blocked else 'SUCCESS'}",
        f"input_lines={input_lines}",
        f"output_lines={'' if output_lines is None else output_lines}",
        f"rules={len(rows)}",
        f"applied={status_counts[STATUS_APPLIED]}",
        f"already_fixed={status_counts[STATUS_ALREADY_FIXED]}",
        f"not_present={status_counts[STATUS_NOT_PRESENT]}",
        f"unexpected={status_counts[STATUS_UNEXPECTED]}",
        f"removed={total_removed}",
        f"added={total_added}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_rules(
    input_path: Path,
    output_path: Path,
    plans: list[RulePlan],
    original_pair_counts: Counter[tuple[str, str]],
) -> tuple[
    Counter[int],
    Counter[int],
    Counter[int],
    Counter[int],
]:
    active_rules = {
        plan.rule.index: plan.rule
        for plan in plans
        if plan.status == STATUS_APPLIED
    }
    keep_only = {
        rule.surface: (rule, set(rule.allowed_readings))
        for rule in active_rules.values()
        if rule.action == "KEEP_ONLY"
    }
    exact_rules = {
        (rule.surface, rule.reading): rule
        for rule in active_rules.values()
        if rule.action != "KEEP_ONLY"
    }

    matched: Counter[int] = Counter()
    removed: Counter[int] = Counter()
    added: Counter[int] = Counter()
    skipped_existing: Counter[int] = Counter()
    emitted_new_pairs: set[tuple[str, str]] = set()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with input_path.open("r", encoding="utf-8") as source, output_path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as output:
        for line_number, line in enumerate(source, start=1):
            reading, left_id, right_id, cost, surface = split_dictionary_line(
                line,
                line_number,
            )

            keep_entry = keep_only.get(surface)
            if keep_entry is not None:
                rule, allowed = keep_entry
                if reading not in allowed:
                    matched[rule.index] += 1
                    removed[rule.index] += 1
                    continue

            rule = exact_rules.get((surface, reading))
            if rule is None:
                output.write(line)
                continue

            matched[rule.index] += 1
            removed[rule.index] += 1

            if rule.action == "DELETE":
                targets: tuple[tuple[str, str], ...] = ()
            else:
                targets = rule_targets(rule)

            for target_surface, target_reading in targets:
                target_pair = (target_surface, target_reading)
                if (
                    original_pair_counts[target_pair] > 0
                    or target_pair in emitted_new_pairs
                ):
                    skipped_existing[rule.index] += 1
                    continue

                output.write(
                    "\t".join(
                        [target_reading, left_id, right_id, cost, target_surface]
                    )
                    + "\n"
                )
                emitted_new_pairs.add(target_pair)
                added[rule.index] += 1

    return matched, removed, added, skipped_existing


def validate_output(
    output_path: Path,
    plans: list[RulePlan],
) -> int:
    pair_counts, surface_readings, line_count = inspect_dictionary(output_path)
    errors: list[str] = []

    for plan in plans:
        rule = plan.rule
        if plan.status not in {STATUS_APPLIED, STATUS_ALREADY_FIXED}:
            continue

        if rule.action == "KEEP_ONLY":
            actual = surface_readings.get(rule.surface, set())
            expected = set(rule.allowed_readings)
            if actual != expected:
                errors.append(
                    f"rule {rule.index}: KEEP_ONLY結果不一致: "
                    f"{rule.surface} expected={sorted(expected)} "
                    f"actual={sorted(actual)}"
                )
            continue

        if pair_counts[(rule.surface, rule.reading)] != 0:
            errors.append(
                f"rule {rule.index}: 補正元が残っています: "
                f"{rule.reading} -> {rule.surface}"
            )

        for target_surface, target_reading in rule_targets(rule):
            if pair_counts[(target_surface, target_reading)] == 0:
                errors.append(
                    f"rule {rule.index}: 補正先がありません: "
                    f"{target_reading} -> {target_surface}"
                )

    if errors:
        raise ValueError("\n".join(errors))

    return line_count


def validate_counts(
    rows: list[dict[str, str]],
    input_lines: int,
    output_lines: int,
) -> None:
    total_removed = sum(int(row["removed"]) for row in rows)
    total_added = sum(int(row["added"]) for row in rows)
    expected_output = input_lines - total_removed + total_added
    if output_lines != expected_output:
        raise ValueError(
            "行数計算が一致しません: "
            f"input={input_lines} removed={total_removed} "
            f"added={total_added} expected_output={expected_output} "
            f"actual_output={output_lines}"
        )


def main() -> None:
    args = parse_args()
    rules = read_rules(args.rules)
    pair_counts, surface_readings, input_lines = inspect_dictionary(args.input)
    plans = plan_rules(rules, pair_counts, surface_readings)

    blocking = [plan for plan in plans if plan.status in BLOCKING_STATUSES]
    if blocking:
        rows = make_report_rows(plans)
        write_report(args.report, rows)
        write_summary(
            args.summary,
            rows,
            input_lines=input_lines,
            output_lines=None,
            blocked=True,
        )
        args.output.unlink(missing_ok=True)

        details = "\n".join(
            f"rule {plan.rule.index}: status={plan.status} "
            f"action={plan.rule.action} surface={plan.rule.surface} "
            f"reading={plan.rule.reading} detail={plan.detail} "
            f"missing={list(plan.missing_targets)}"
            for plan in blocking
        )
        raise ValueError(
            "辞書更新により自動適用できないルールがあります。"
            "SUDACHI_CORRECTION_REPORT.tsvを確認してください。\n"
            + details
        )

    matched, removed, added, skipped_existing = apply_rules(
        args.input,
        args.output,
        plans,
        pair_counts,
    )
    output_lines = validate_output(args.output, plans)
    rows = make_report_rows(
        plans,
        matched=matched,
        removed=removed,
        added=added,
        skipped_existing=skipped_existing,
    )
    validate_counts(rows, input_lines, output_lines)
    write_report(args.report, rows)
    write_summary(
        args.summary,
        rows,
        input_lines=input_lines,
        output_lines=output_lines,
        blocked=False,
    )

    status_counts = Counter(row["status"] for row in rows)
    print(f"Input dictionary lines: {input_lines}")
    print(f"Output dictionary lines: {output_lines}")
    print(f"Applied rules: {status_counts[STATUS_APPLIED]}")
    print(f"Already fixed rules: {status_counts[STATUS_ALREADY_FIXED]}")
    print(f"Not present rules: {status_counts[STATUS_NOT_PRESENT]}")
    print(f"Unexpected rules: {status_counts[STATUS_UNEXPECTED]}")
    print(f"Removed lines: {sum(int(row['removed']) for row in rows)}")
    print(f"Added lines: {sum(int(row['added']) for row in rows)}")


if __name__ == "__main__":
    main()
