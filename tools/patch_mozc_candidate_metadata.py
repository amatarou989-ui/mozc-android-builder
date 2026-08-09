#!/usr/bin/env python3
"""Expose Mozc candidate ranking metadata through CandidateWord.

This builder-side patch is intentionally fail-closed. It patches the Mozc
source checked out by GitHub Actions so Futatsumugi can read ranking signals
from the normal evalCommand protobuf response without adding a new JNI entry
point.

Modern Mozc revisions can serialize CandidateWord from more than one code path.
Do not assume a file/function name and do not force a single match. Locate every
strong CandidateWord serializer by same-proto writes in a tight local scope,
then attach the metadata block to each serializer.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


PROTO_MARKER = "Futatsumugi private ranking metadata."
CPP_MARKER = "Futatsumugi ranking metadata: keep this block in sync with candidate_window.proto."

PROTO_FIELDS = """  // Futatsumugi private ranking metadata.\n  // Field numbers 200-211 are intentionally kept outside Mozc's normal CandidateWord range.\n  // Older clients safely ignore these unknown proto2 fields.\n  optional int32 futatsumugi_lid = 200 [default = -1];\n  optional int32 futatsumugi_rid = 201 [default = -1];\n  optional int32 futatsumugi_cost = 202;\n  optional int32 futatsumugi_wcost = 203;\n  optional int32 futatsumugi_structure_cost = 204;\n  optional string futatsumugi_content_key = 205;\n  optional string futatsumugi_content_value = 206;\n  optional uint32 futatsumugi_raw_attributes = 207;\n  optional uint32 futatsumugi_source_info = 208;\n  optional uint64 futatsumugi_consumed_key_size = 209;\n  repeated uint32 futatsumugi_inner_segment_boundary = 210 [packed = true];\n  optional int32 futatsumugi_cost_before_rescoring = 211;\n\n"""

REQUIRED_CANDIDATE_MEMBERS = (
    "content_key",
    "content_value",
    "cost",
    "wcost",
    "structure_cost",
    "lid",
    "rid",
    "attributes",
    "source_info",
    "consumed_key_size",
    "inner_segment_boundary",
    "cost_before_rescoring",
)

SET_VALUE_RE = re.compile(
    r"(?P<proto>[A-Za-z_]\w*)\s*->\s*set_value\s*\(\s*"
    r"(?P<candidate>[A-Za-z_]\w*)\s*\.\s*value\s*\)\s*;",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class SerializerMatch:
    path: Path
    start: int
    end: int
    proto_var: str
    candidate_var: str
    score: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class PatchResult:
    proto_changed: bool
    cpp_changed: bool
    candidate_definition: Path
    proto_path: Path
    serializers: tuple[SerializerMatch, ...]
    patched_cpp_paths: tuple[Path, ...]

    # Backward-compatible accessors used by the workflow/tests.
    @property
    def cpp_path(self) -> Path:
        return self.serializers[0].path

    @property
    def serializer_proto_var(self) -> str:
        return self.serializers[0].proto_var

    @property
    def serializer_candidate_var(self) -> str:
        return self.serializers[0].candidate_var

    @property
    def serializer_score(self) -> int:
        return self.serializers[0].score

    @property
    def serializer_evidence(self) -> tuple[str, ...]:
        return self.serializers[0].evidence


def _find_proto(mozc_src: Path) -> Path:
    preferred = mozc_src / "protocol" / "candidate_window.proto"
    if preferred.is_file():
        return preferred
    matches = []
    for path in mozc_src.rglob("candidate_window.proto"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "message CandidateWord {" in text:
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    raise RuntimeError(
        "Could not uniquely locate candidate_window.proto containing CandidateWord. "
        f"matches={matches}"
    )


def _same_proto_write(window: str, proto: str, method: str) -> bool:
    return re.search(rf"\b{re.escape(proto)}\s*->\s*{re.escape(method)}\s*\(", window) is not None


def _same_candidate_member(window: str, candidate: str, member: str) -> bool:
    return re.search(rf"\b{re.escape(candidate)}\s*\.\s*{re.escape(member)}\b", window) is not None


def _serializer_score(text: str, match: re.Match[str]) -> tuple[int, tuple[str, ...]]:
    # Deliberately tight: v4 used +/-5000 bytes, causing two nearby serializers
    # to inherit each other's evidence. Same-proto checks prevent that.
    lo = max(0, match.start() - 1400)
    hi = min(len(text), match.end() + 1800)
    window = text[lo:hi]
    proto = match.group("proto")
    candidate = match.group("candidate")
    score = 0
    evidence: list[str] = []

    checks = (
        ("set_id", 4, "id"),
        ("set_index", 3, "index"),
        ("set_key", 4, "key"),
        ("add_attributes", 6, "attributes"),
        ("set_num_segments_in_candidate", 10, "num_segments"),
        ("set_log", 2, "log"),
    )
    for method, points, label in checks:
        if _same_proto_write(window, proto, method):
            score += points
            evidence.append(label)

    # Strongest evidence: the pointer itself is typed as CandidateWord in the
    # nearby function signature/body.
    if re.search(
        rf"(?:commands::)?CandidateWord\s*\*\s*{re.escape(proto)}\b",
        window,
    ):
        score += 12
        evidence.append("typed_candidate_word")

    member_checks = (
        ("attributes", 3, "candidate_attributes"),
        ("content_key", 2, "content_key"),
        ("content_value", 1, "content_value"),
    )
    for member, points, label in member_checks:
        if _same_candidate_member(window, candidate, member):
            score += points
            evidence.append(label)

    for needle, points, label in (
        ("USER_DICTIONARY", 2, "user_dictionary"),
        ("TYPING_CORRECTION", 1, "typing_correction"),
        ("SPELLING_CORRECTION", 1, "spelling_correction"),
    ):
        if needle in window:
            score += points
            evidence.append(label)

    return score, tuple(evidence)


def _is_strong_serializer(item: SerializerMatch) -> bool:
    evidence = set(item.evidence)
    # Either an explicit CandidateWord pointer type, or the exact proto receives
    # both segment count and attributes. Rendered Candidates::Candidate paths do
    # not satisfy this.
    structurally_candidate_word = (
        "typed_candidate_word" in evidence
        or {"num_segments", "attributes"}.issubset(evidence)
    )
    return structurally_candidate_word and item.score >= 16


def _find_candidate_serializers(mozc_src: Path) -> tuple[SerializerMatch, ...]:
    found: list[SerializerMatch] = []
    near: list[str] = []
    for path in mozc_src.rglob("*.cc"):
        if not path.is_file():
            continue
        path_text = path.as_posix()
        if "/test/" in path_text or path.name.endswith("_test.cc"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "set_value" not in text:
            continue
        for match in SET_VALUE_RE.finditer(text):
            score, evidence = _serializer_score(text, match)
            item = SerializerMatch(
                path=path,
                start=match.start(),
                end=match.end(),
                proto_var=match.group("proto"),
                candidate_var=match.group("candidate"),
                score=score,
                evidence=evidence,
            )
            if _is_strong_serializer(item):
                found.append(item)
            elif score >= 8:
                near.append(
                    f"{path}:{match.start()}:score={score}:"
                    f"proto={item.proto_var}:candidate={item.candidate_var}:"
                    f"evidence={','.join(evidence)}"
                )

    if not found:
        raise RuntimeError(
            "Could not locate a strong Mozc CandidateWord serializer. "
            "Expected the same proto variable to receive CandidateWord-specific "
            "writes such as set_num_segments_in_candidate/add_attributes or to "
            "be explicitly typed CandidateWord*. "
            f"near_matches={near[:30]}"
        )

    # Keep every independently validated serializer. Modern Mozc can have more
    # than one CandidateWord output path. De-duplicate only exact same anchors.
    unique: dict[tuple[str, int, str, str], SerializerMatch] = {}
    for item in found:
        unique[(str(item.path), item.start, item.proto_var, item.candidate_var)] = item
    result = tuple(sorted(unique.values(), key=lambda x: (str(x.path), x.start)))
    return result


def _find_candidate_definition(mozc_src: Path) -> Path:
    candidates = (
        mozc_src / "converter" / "candidate.h",
        mozc_src / "converter" / "segments.h",
    )
    for path in candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if all(member in text for member in REQUIRED_CANDIDATE_MEMBERS):
            return path
    checked = ", ".join(str(path) for path in candidates)
    raise RuntimeError(
        "Mozc Candidate layout did not contain the expected ranking members. "
        f"Checked: {checked}. Upstream source may have changed; stop rather than patching blindly."
    )


def _patch_proto(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if PROTO_MARKER in text:
        return False
    message_start = text.find("message CandidateWord {")
    if message_start < 0:
        raise RuntimeError(f"CandidateWord message was not found: {path}")
    next_message = text.find("\nmessage ", message_start + 1)
    search_end = len(text) if next_message < 0 else next_message
    block = text[message_start:search_end]
    for field_number in range(200, 212):
        if f"= {field_number}" in block:
            raise RuntimeError(
                f"CandidateWord field number {field_number} is already used. "
                "Choose a new private range instead of colliding with upstream Mozc."
            )
    anchor = "  optional string log = 100;"
    anchor_pos = block.find(anchor)
    if anchor_pos < 0:
        raise RuntimeError(
            "CandidateWord debug-log anchor was not found. "
            "Upstream candidate_window.proto may have changed."
        )
    insert_at = message_start + anchor_pos
    text = text[:insert_at] + PROTO_FIELDS + text[insert_at:]
    path.write_text(text, encoding="utf-8")
    return True


def _metadata_cpp(proto_var: str, candidate_var: str) -> str:
    p = proto_var
    c = candidate_var
    return f"""
  // Futatsumugi ranking metadata: keep this block in sync with candidate_window.proto.
  {p}->set_futatsumugi_lid({c}.lid);
  {p}->set_futatsumugi_rid({c}.rid);
  {p}->set_futatsumugi_cost({c}.cost);
  {p}->set_futatsumugi_wcost({c}.wcost);
  {p}->set_futatsumugi_structure_cost({c}.structure_cost);
  {p}->set_futatsumugi_content_key({c}.content_key);
  {p}->set_futatsumugi_content_value({c}.content_value);
  {p}->set_futatsumugi_raw_attributes(
      static_cast<uint32_t>({c}.attributes));
  {p}->set_futatsumugi_source_info(
      static_cast<uint32_t>({c}.source_info));
  {p}->set_futatsumugi_consumed_key_size(
      static_cast<uint64_t>({c}.consumed_key_size));
  for (const auto encoded_boundary : {c}.inner_segment_boundary) {{
    {p}->add_futatsumugi_inner_segment_boundary(
        static_cast<uint32_t>(encoded_boundary));
  }}
  {p}->set_futatsumugi_cost_before_rescoring(
      {c}.cost_before_rescoring);
"""


def _has_local_metadata(text: str, anchor_end: int, proto_var: str) -> bool:
    tail = text[anchor_end: min(len(text), anchor_end + 2200)]
    return (
        CPP_MARKER in tail
        and f"{proto_var}->set_futatsumugi_lid(" in tail
        and f"{proto_var}->set_futatsumugi_cost_before_rescoring(" in tail
    )


def _patch_cpp_matches(path: Path, matches: list[SerializerMatch]) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False

    # Re-discover against current text, then apply from the end of the file so
    # earlier offsets remain valid. Match by approximate original location and
    # variable pair; this also makes a second invocation idempotent.
    current = list(SET_VALUE_RE.finditer(text))
    insertions: list[tuple[int, str]] = []
    for wanted in matches:
        compatible = [
            m for m in current
            if m.group("proto") == wanted.proto_var
            and m.group("candidate") == wanted.candidate_var
            and abs(m.start() - wanted.start) < 600
        ]
        if len(compatible) != 1:
            raise RuntimeError(
                "CandidateWord serializer anchor changed before patching: "
                f"{path}; proto={wanted.proto_var}; candidate={wanted.candidate_var}; "
                f"compatible={[(m.start(), m.end()) for m in compatible]}"
            )
        anchor = compatible[0]
        if _has_local_metadata(text, anchor.end(), wanted.proto_var):
            continue
        insertions.append(
            (anchor.end(), _metadata_cpp(wanted.proto_var, wanted.candidate_var))
        )

    for pos, block in sorted(insertions, key=lambda item: item[0], reverse=True):
        text = text[:pos] + block + text[pos:]
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def _patch_all_cpp(serializers: tuple[SerializerMatch, ...]) -> tuple[bool, tuple[Path, ...]]:
    by_path: dict[Path, list[SerializerMatch]] = {}
    for item in serializers:
        by_path.setdefault(item.path, []).append(item)
    changed = False
    patched_paths: list[Path] = []
    for path, matches in by_path.items():
        path_changed = _patch_cpp_matches(path, matches)
        changed = changed or path_changed
        patched_paths.append(path)
    return changed, tuple(sorted(patched_paths, key=str))


def patch_mozc_source(mozc_src: Path) -> PatchResult:
    mozc_src = mozc_src.resolve()
    proto = _find_proto(mozc_src)
    serializers = _find_candidate_serializers(mozc_src)
    candidate_definition = _find_candidate_definition(mozc_src)
    proto_changed = _patch_proto(proto)
    cpp_changed, patched_cpp_paths = _patch_all_cpp(serializers)
    return PatchResult(
        proto_changed=proto_changed,
        cpp_changed=cpp_changed,
        candidate_definition=candidate_definition,
        proto_path=proto,
        serializers=serializers,
        patched_cpp_paths=patched_cpp_paths,
    )


def write_report(path: Path, result: PatchResult, mozc_src: Path) -> None:
    status = "APPLIED" if (result.proto_changed or result.cpp_changed) else "ALREADY_APPLIED"
    lines = [
        "result=SUCCESS",
        f"status={status}",
        f"mozc_src={mozc_src.resolve()}",
        f"candidate_definition={result.candidate_definition}",
        f"proto_path={result.proto_path}",
        # Keep cpp_path for old workflow consumers, plus complete multi-path data.
        f"cpp_path={result.cpp_path}",
        f"serializer_count={len(result.serializers)}",
        f"cpp_paths={'|'.join(str(p) for p in result.patched_cpp_paths)}",
        f"serializer_proto_var={result.serializer_proto_var}",
        f"serializer_candidate_var={result.serializer_candidate_var}",
        f"serializer_score={result.serializer_score}",
        f"serializer_evidence={','.join(result.serializer_evidence)}",
        f"proto_changed={str(result.proto_changed).lower()}",
        f"cpp_changed={str(result.cpp_changed).lower()}",
        "candidate_word_private_fields=200-211",
        "metadata=lid,rid,cost,wcost,structure_cost,content_key,content_value,raw_attributes,source_info,consumed_key_size,inner_segment_boundary,cost_before_rescoring",
        "standard_fields_reused=id(1),index(2),key(3),value(4),attributes(6),num_segments_in_candidate(7)",
    ]
    for index, item in enumerate(result.serializers, start=1):
        lines.extend(
            [
                f"serializer_{index}_path={item.path}",
                f"serializer_{index}_offset={item.start}",
                f"serializer_{index}_proto_var={item.proto_var}",
                f"serializer_{index}_candidate_var={item.candidate_var}",
                f"serializer_{index}_score={item.score}",
                f"serializer_{index}_evidence={','.join(item.evidence)}",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mozc-src", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = patch_mozc_source(args.mozc_src)
    if args.report is not None:
        write_report(args.report, result, args.mozc_src)
    print(
        "Mozc candidate metadata patch: "
        f"proto_changed={result.proto_changed}, cpp_changed={result.cpp_changed}, "
        f"candidate_definition={result.candidate_definition}, "
        f"proto_path={result.proto_path}, serializers={len(result.serializers)}, "
        f"cpp_paths={[str(p) for p in result.patched_cpp_paths]}"
    )
    for index, item in enumerate(result.serializers, start=1):
        print(
            f"  serializer[{index}] path={item.path} offset={item.start} "
            f"vars={item.proto_var}/{item.candidate_var} score={item.score} "
            f"evidence={item.evidence}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
