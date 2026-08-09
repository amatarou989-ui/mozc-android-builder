#!/usr/bin/env python3
"""Expose Mozc candidate ranking metadata through CandidateWord.

This builder-side patch is intentionally small and fail-closed.  It patches the
Mozc source checked out by GitHub Actions, so Futatsumugi can read ranking
signals from the normal evalCommand protobuf response without adding a new JNI
entry point.

The CandidateWord serializer has moved between Mozc revisions.  Do not depend
on a specific file/function name; locate the serializer by the actual protobuf
write from Segment::Candidate.value and surrounding CandidateWord-only signals.
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

# Capture the actual proto pointer and Segment::Candidate variable instead of
# assuming historical names such as candidate_word_proto/segment_candidate.
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
    cpp_path: Path
    serializer_proto_var: str
    serializer_candidate_var: str
    serializer_score: int
    serializer_evidence: tuple[str, ...]


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


def _serializer_score(text: str, match: re.Match[str]) -> tuple[int, tuple[str, ...]]:
    # Keep a generous local window.  CandidateWord serializers are compact,
    # whereas unrelated candidate-window writers normally do not emit the
    # CandidateWord attributes/segment-count fields.
    lo = max(0, match.start() - 5000)
    hi = min(len(text), match.end() + 5000)
    window = text[lo:hi]
    score = 0
    evidence: list[str] = []

    checks = (
        ("CandidateWord", 6, "CandidateWord"),
        ("num_segments_in_candidate", 6, "num_segments"),
        ("add_attributes", 4, "attributes"),
        ("USER_DICTIONARY", 3, "user_dictionary"),
        ("content_key", 2, "content_key"),
        ("TYPING_CORRECTION", 2, "typing_correction"),
        ("SPELLING_CORRECTION", 2, "spelling_correction"),
    )
    for needle, points, label in checks:
        if needle in window:
            score += points
            evidence.append(label)

    # A file in engine/session is much more likely to be the command-output
    # serializer than converters/tests that happen to construct protos.
    return score, tuple(evidence)


def _find_candidate_serializer(mozc_src: Path) -> SerializerMatch:
    candidates: list[SerializerMatch] = []
    # Serializer implementations have historically lived in session/ and
    # engine/. Search all production C++ as a fallback so future moves are OK.
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
            # Require at least one CandidateWord-specific signal.  This rejects
            # the older Candidates::Candidate renderer serializer.
            if score < 10:
                continue
            candidates.append(
                SerializerMatch(
                    path=path,
                    start=match.start(),
                    end=match.end(),
                    proto_var=match.group("proto"),
                    candidate_var=match.group("candidate"),
                    score=score,
                    evidence=evidence,
                )
            )

    if not candidates:
        # Add useful diagnostics directly to the exception. GitHub Actions will
        # then show the closest source locations instead of only "matches=[]".
        near: list[str] = []
        for path in mozc_src.rglob("*.cc"):
            if not path.is_file() or path.name.endswith("_test.cc"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "CandidateWord" in text or "num_segments_in_candidate" in text:
                near.append(str(path))
        raise RuntimeError(
            "Could not locate the Mozc CandidateWord serializer by protobuf writes. "
            "Expected a '<proto>->set_value(<SegmentCandidate>.value)' write near "
            "CandidateWord attributes/segment metadata. "
            f"candidate_word_related_files={near[:20]}"
        )

    candidates.sort(key=lambda item: (-item.score, str(item.path), item.start))
    best = candidates[0]
    tied = [item for item in candidates if item.score == best.score]
    # Multiple hits in one file can occur when the same helper is called in
    # different branches, but multiple distinct best locations are ambiguous.
    unique_locations = {(str(item.path), item.start) for item in tied}
    if len(unique_locations) != 1:
        diagnostics = [
            f"{item.path}:{item.start}:score={item.score}:evidence={','.join(item.evidence)}"
            for item in tied[:20]
        ]
        raise RuntimeError(
            "Could not uniquely locate the Mozc CandidateWord serializer; "
            f"best_matches={diagnostics}"
        )
    return best


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
    # Modern Mozc has the debug log at field 100. Insert before it to keep the
    # private block visibly separate from upstream fields.
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
    return f"""\n  // Futatsumugi ranking metadata: keep this block in sync with candidate_window.proto.\n  {p}->set_futatsumugi_lid({c}.lid);\n  {p}->set_futatsumugi_rid({c}.rid);\n  {p}->set_futatsumugi_cost({c}.cost);\n  {p}->set_futatsumugi_wcost({c}.wcost);\n  {p}->set_futatsumugi_structure_cost({c}.structure_cost);\n  {p}->set_futatsumugi_content_key({c}.content_key);\n  {p}->set_futatsumugi_content_value({c}.content_value);\n  {p}->set_futatsumugi_raw_attributes(\n      static_cast<uint32_t>({c}.attributes));\n  {p}->set_futatsumugi_source_info(\n      static_cast<uint32_t>({c}.source_info));\n  {p}->set_futatsumugi_consumed_key_size(\n      static_cast<uint64_t>({c}.consumed_key_size));\n  for (const uint32_t encoded_boundary : {c}.inner_segment_boundary) {{\n    {p}->add_futatsumugi_inner_segment_boundary(encoded_boundary);\n  }}\n  {p}->set_futatsumugi_cost_before_rescoring(\n      {c}.cost_before_rescoring);\n"""


def _patch_cpp(match: SerializerMatch) -> bool:
    path = match.path
    text = path.read_text(encoding="utf-8")
    if CPP_MARKER in text:
        return False
    # Re-find after proto patch to guard against stale offsets if this function
    # is reused independently in tests/tools.
    found = None
    for candidate in SET_VALUE_RE.finditer(text):
        if (
            candidate.group("proto") == match.proto_var
            and candidate.group("candidate") == match.candidate_var
            and abs(candidate.start() - match.start) < 100
        ):
            found = candidate
            break
    if found is None:
        raise RuntimeError(
            f"CandidateWord set_value anchor changed before patching: {path}"
        )
    insert_at = found.end()
    text = (
        text[:insert_at]
        + _metadata_cpp(match.proto_var, match.candidate_var)
        + text[insert_at:]
    )
    path.write_text(text, encoding="utf-8")
    return True


def patch_mozc_source(mozc_src: Path) -> PatchResult:
    mozc_src = mozc_src.resolve()
    proto = _find_proto(mozc_src)
    serializer = _find_candidate_serializer(mozc_src)
    candidate_definition = _find_candidate_definition(mozc_src)
    proto_changed = _patch_proto(proto)
    cpp_changed = _patch_cpp(serializer)
    return PatchResult(
        proto_changed=proto_changed,
        cpp_changed=cpp_changed,
        candidate_definition=candidate_definition,
        proto_path=proto,
        cpp_path=serializer.path,
        serializer_proto_var=serializer.proto_var,
        serializer_candidate_var=serializer.candidate_var,
        serializer_score=serializer.score,
        serializer_evidence=serializer.evidence,
    )


def write_report(path: Path, result: PatchResult, mozc_src: Path) -> None:
    status = "APPLIED" if (result.proto_changed or result.cpp_changed) else "ALREADY_APPLIED"
    lines = [
        "result=SUCCESS",
        f"status={status}",
        f"mozc_src={mozc_src.resolve()}",
        f"candidate_definition={result.candidate_definition}",
        f"proto_path={result.proto_path}",
        f"cpp_path={result.cpp_path}",
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
        f"proto_path={result.proto_path}, cpp_path={result.cpp_path}, "
        f"serializer={result.serializer_proto_var}/{result.serializer_candidate_var}, "
        f"score={result.serializer_score}, evidence={result.serializer_evidence}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
