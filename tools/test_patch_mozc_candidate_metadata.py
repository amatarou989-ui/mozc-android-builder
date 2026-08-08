import tempfile
import unittest
from pathlib import Path

from patch_mozc_candidate_metadata import patch_mozc_source


PROTO = """syntax = \"proto2\";\nmessage CandidateWord {\n  optional int32 id = 1;\n  optional uint32 index = 2;\n  optional string key = 3;\n  optional string value = 4;\n  repeated uint32 attributes = 6;\n  optional int32 num_segments_in_candidate = 7;\n  optional string log = 100;\n}\nmessage CandidateList {}\n"""

CPP = """#include <cstdint>\nvoid FillCandidateWord(const Segment::Candidate &segment_candidate,\n                       const int id, const int index,\n                       const absl::string_view base_key,\n                       commands::CandidateWord *candidate_word_proto) {\n  candidate_word_proto->set_id(id);\n  candidate_word_proto->set_value(segment_candidate.value);\n  // number of segments\n  candidate_word_proto->set_num_segments_in_candidate(1);\n#ifndef NDEBUG\n  candidate_word_proto->set_log(segment_candidate.DebugString());\n#endif\n}\nvoid NextFunction() {}\n"""

CANDIDATE = """struct Candidate {\n  std::string content_key;\n  std::string content_value;\n  int cost;\n  int wcost;\n  int structure_cost;\n  int lid;\n  int rid;\n  uint32_t attributes;\n  uint32_t source_info;\n  size_t consumed_key_size;\n  std::vector<uint32_t> inner_segment_boundary;\n  int32_t cost_before_rescoring;\n};\n"""


class PatchMozcCandidateMetadataTest(unittest.TestCase):
    def make_tree(self, root: Path) -> Path:
        src = root / "src"
        (src / "protocol").mkdir(parents=True)
        (src / "session" / "internal").mkdir(parents=True)
        (src / "converter").mkdir(parents=True)
        (src / "protocol" / "candidate_window.proto").write_text(PROTO, encoding="utf-8")
        (src / "session" / "internal" / "session_output.cc").write_text(CPP, encoding="utf-8")
        (src / "converter" / "candidate.h").write_text(CANDIDATE, encoding="utf-8")
        return src

    def test_patch_adds_private_proto_fields_and_cpp_setters(self):
        with tempfile.TemporaryDirectory() as temporary:
            src = self.make_tree(Path(temporary))
            result = patch_mozc_source(src)
            self.assertTrue(result.proto_changed)
            self.assertTrue(result.cpp_changed)
            proto = (src / "protocol" / "candidate_window.proto").read_text(encoding="utf-8")
            cpp = (src / "session" / "internal" / "session_output.cc").read_text(encoding="utf-8")
            self.assertIn("futatsumugi_lid = 200", proto)
            self.assertIn("futatsumugi_cost_before_rescoring = 211", proto)
            self.assertIn("set_futatsumugi_structure_cost", cpp)
            self.assertIn("add_futatsumugi_inner_segment_boundary", cpp)

    def test_patch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            src = self.make_tree(Path(temporary))
            patch_mozc_source(src)
            proto_before = (src / "protocol" / "candidate_window.proto").read_text(encoding="utf-8")
            cpp_before = (src / "session" / "internal" / "session_output.cc").read_text(encoding="utf-8")
            result = patch_mozc_source(src)
            self.assertFalse(result.proto_changed)
            self.assertFalse(result.cpp_changed)
            self.assertEqual(proto_before, (src / "protocol" / "candidate_window.proto").read_text(encoding="utf-8"))
            self.assertEqual(cpp_before, (src / "session" / "internal" / "session_output.cc").read_text(encoding="utf-8"))

    def test_private_field_number_collision_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            src = self.make_tree(Path(temporary))
            proto = src / "protocol" / "candidate_window.proto"
            proto.write_text(
                PROTO.replace(
                    "  optional string log = 100;",
                    "  optional int32 upstream_future_field = 205;\n  optional string log = 100;",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                patch_mozc_source(src)

    def test_missing_candidate_member_stops_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as temporary:
            src = self.make_tree(Path(temporary))
            candidate = src / "converter" / "candidate.h"
            candidate.write_text(CANDIDATE.replace("  int structure_cost;\n", ""), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                patch_mozc_source(src)


if __name__ == "__main__":
    unittest.main()
