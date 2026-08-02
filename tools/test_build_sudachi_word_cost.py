#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from build_sudachi_word_cost import (
    BuildStats,
    ENTRY_STRUCT,
    HEADER_STRUCT,
    key_hash,
    load_rows,
    verify_binary,
    write_binary,
)


class SudachiWordCostBuilderTest(unittest.TestCase):
    def test_duplicate_pair_keeps_lowest_cost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "dict.tsv"
            source.write_text(
                "ごだん\t1\t1\t5000\t五段\n"
                "ごだん\t1\t1\t1200\t五段\n"
                "ごだん\t1\t1\t9000\t塵弾\n",
                encoding="utf-8",
            )
            stats = BuildStats()
            rows = load_rows(source, stats)
            self.assertEqual((1200, 2), rows[("ごだん", "五段")])
            self.assertEqual((9000, 1), rows[("ごだん", "塵弾")])
            self.assertEqual(1, stats.duplicate_pairs)

    def test_binary_is_fixed_layout_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cost.bin"
            rows = {
                ("ごだん", "五段"): (1200, 1),
                ("ごだん", "塵弾"): (9000, 1),
                ("ふだん", "普段"): (800, 1),
            }
            count = write_binary(output, rows)
            self.assertEqual(3, count)
            self.assertEqual(HEADER_STRUCT.size + 3 * ENTRY_STRUCT.size, output.stat().st_size)
            verify_binary(output, 3)

    def test_hash_distinguishes_reading_and_surface(self) -> None:
        self.assertNotEqual(key_hash("ごだん", "五段"), key_hash("ごだん", "塵弾"))
        self.assertNotEqual(key_hash("ごだん", "五段"), key_hash("ごたん", "五段"))


if __name__ == "__main__":
    unittest.main()
