#!/usr/bin/env python3
"""Expose Mozc candidate ranking metadata through CandidateWord.

This builder-side patch is intentionally small and fail-closed.  It patches the
Mozc source checked out by GitHub Actions, so Futatsumugi can read ranking
signals from the normal evalCommand protobuf response without adding a new JNI
entry point.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


PROTO_MARKER = "Futatsumugi private ranking metadata."
CPP_MARKER = "Futatsumugi ranking metadata: keep this block in sync with candidate_window.proto."

PROTO_FIELDS = """  // Futatsumugi private ranking metadata.\n  // Field numbers 200-211 are intentionally kept outside Mozc's normal CandidateWord range.\n  // Older clients safely ignore these unknown proto2 fields.\n  optional int32 futatsumugi_lid = 200 [default = -1];\n  optional int32 futatsumugi_rid = 201 [default = -1];\n  optional int32 futatsumugi_cost = 202;\n  optional int32 futatsumugi_wcost = 203;\n  optional int32 futatsumugi_structure_cost = 204;\n  optional string futatsumugi_content_key = 205;\n  optional string futatsumugi_content_value = 206;\n  optional uint32 futatsumugi_raw_attributes = 207;\n  optional uint32 futatsumugi_source_info = 208;\n  optional uint64 futatsumugi_consumed_key_size = 209;\n  repeated uint32 futatsumugi_inner_segment_boundary = 210 [packed = true];\n  optional int32 futatsumugi_cost_before_rescoring = 211;\n\n"""

CPP_FIELDS = """  // Futatsumugi ranking metadata: keep this block in sync with candidate_window.proto.\n  candidate_word_proto->set_futatsumugi_lid(segment_candidate.lid);\n  candidate_word_proto->set_futatsumugi_rid(segment_candidate.rid);\n  candidate_word_proto->set_futatsumugi_cost(segment_candidate.cost);\n  candidate_word_proto->set_futatsumugi_wcost(segment_candidate.wcost);\n  candidate_word_proto->set_futatsumugi_structure_cost(\n      segment_candidate.structure_cost);\n  candidate_word_proto->set_futatsumugi_content_key(\n      segment_candidate.content_key);\n  candidate_word_proto->set_futatsumugi_content_value(\n      segment_candidate.content_value);\n  candidate_word_proto->set_futatsumugi_raw_attributes(\n      static_cast<uint32_t>(segment_candidate.attributes));\n  candidate_word_proto->set_futatsumugi_source_info(\n      static_cast<uint32_t>(segment_candidate.source_info));\n  candidate_word_proto->set_futatsumugi_consumed_key_size(\n      static_cast<uint64_t>(segment_candidate.consumed_key_size));\n  for (const uint32_t encoded_boundary :\n       segment_candidate.inner_segment_boundary) {\n    candidate_word_proto->add_futatsumugi_inner_segment_boundary(\n        encoded_boundary);\n  }\n  candidate_word_proto->set_futatsumugi_cost_before_rescoring(\n      segment_candidate.cost_before_rescoring);\n\n"""

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


@dataclass(frozen=True)
class PatchResult:
    proto_changed: bool
    cpp_changed: bool
    candidate_definition: Path
    proto_path: Path
    cpp_path: Path


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


def _find_session_output(mozc_src: Path) -> Path:
    preferred = (
        mozc_src / "session" / "internal" / "session_output.cc",
        mozc_src / "session" / "session_output.cc",
    )
    for path in preferred:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if "void FillCandidateWord(" in text and "set_num_segments_in_candidate" in text:
                return path

    matches = []
    session_root = mozc_src / "session"
    search_root = session_root if session_root.is_dir() else mozc_src
    for path in search_root.rglob("*.cc"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "void FillCandidateWord(" in text and "set_num_segments_in_candidate" in text:
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    raise RuntimeError(
        "Could not uniquely locate the Mozc CandidateWord serializer. "
        "Searched for FillCandidateWord + set_num_segments_in_candidate; "
        f"matches={matches}"
    )


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


def _patch_cpp(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if CPP_MARKER in text:
        return False
    function_start = text.find("void FillCandidateWord(")
    if function_start < 0:
        raise RuntimeError(f"FillCandidateWord was not found: {path}")
    next_function = text.find("\nvoid ", function_start + 1)
    search_end = len(text) if next_function < 0 else next_function
    block = text[function_start:search_end]
    anchor = "#ifndef NDEBUG"
    anchor_pos = block.find(anchor)
    if anchor_pos < 0:
        raise RuntimeError(
            "FillCandidateWord debug anchor was not found. "
            "Upstream session_output.cc may have changed."
        )
    insert_at = function_start + anchor_pos
    text = text[:insert_at] + CPP_FIELDS + text[insert_at:]
    path.write_text(text, encoding="utf-8")
    return True


def patch_mozc_source(mozc_src: Path) -> PatchResult:
    mozc_src = mozc_src.resolve()
    proto = _find_proto(mozc_src)
    cpp = _find_session_output(mozc_src)
    candidate_definition = _find_candidate_definition(mozc_src)
    proto_changed = _patch_proto(proto)
    cpp_changed = _patch_cpp(cpp)
    return PatchResult(
        proto_changed=proto_changed,
        cpp_changed=cpp_changed,
        candidate_definition=candidate_definition,
        proto_path=proto,
        cpp_path=cpp,
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
        f"proto_path={result.proto_path}, cpp_path={result.cpp_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
