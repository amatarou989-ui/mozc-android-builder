#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

MAGIC = b"FSCOST01"
VERSION = 1
HASH_ALGORITHM_ID = 1
NORMALIZATION_ID = 1  # Unicode NFKC
HEADER_STRUCT = struct.Struct("<8sIIIIQ")
ENTRY_STRUCT = struct.Struct("<qi")  # signed int64 hash, signed int32 minimum cost
FNV64_OFFSET_BASIS = 0xCBF29CE484222325
FNV64_PRIME = 0x100000001B3
FNV64_MASK = 0xFFFFFFFFFFFFFFFF
KEY_SEPARATOR = 0x1F


@dataclass
class BuildStats:
    input_rows: int = 0
    distinct_pairs: int = 0
    duplicate_pairs: int = 0
    normalization_collisions: int = 0
    exported_entries: int = 0
    min_cost: int = 0
    max_cost: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a compact Sudachi reading+surface word-cost lookup for Futatsumugi."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-bin", required=True, type=Path)
    parser.add_argument("--output-tsv", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-name", default="mozcdic-ut-sudachidict corrected dictionary")
    parser.add_argument("--source-ref", default="")
    parser.add_argument("--source-commit", default="")
    return parser.parse_args()


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def fnv1a_utf16_code_units(parts: tuple[str, ...]) -> int:
    value = FNV64_OFFSET_BASIS
    for part_index, part in enumerate(parts):
        if part_index:
            value = ((value ^ KEY_SEPARATOR) * FNV64_PRIME) & FNV64_MASK
        encoded = part.encode("utf-16-le", errors="strict")
        for offset in range(0, len(encoded), 2):
            code_unit = encoded[offset] | (encoded[offset + 1] << 8)
            value = ((value ^ code_unit) * FNV64_PRIME) & FNV64_MASK
    return value - (1 << 64) if value >= (1 << 63) else value


def key_hash(reading: str, surface: str) -> int:
    return fnv1a_utf16_code_units((reading, surface))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path, stats: BuildStats) -> dict[tuple[str, str], tuple[int, int]]:
    # value = (minimum cost, number of source rows merged into this normalized key)
    rows: dict[tuple[str, str], tuple[int, int]] = {}
    original_by_normalized: dict[tuple[str, str], set[tuple[str, str]]] = {}

    with path.open("r", encoding="utf-8", newline="") as source:
        for line_number, raw in enumerate(source, start=1):
            stats.input_rows += 1
            columns = raw.rstrip("\r\n").split("\t")
            if len(columns) != 5:
                raise ValueError(
                    f"dictionary row {line_number} must have 5 columns; actual={len(columns)}"
                )
            reading_raw, _left_id, _right_id, cost_raw, surface_raw = columns
            reading = normalize(reading_raw)
            surface = normalize(surface_raw)
            if not reading or not surface:
                continue
            try:
                cost = int(cost_raw)
            except ValueError as error:
                raise ValueError(
                    f"dictionary row {line_number} has invalid cost: {cost_raw!r}"
                ) from error
            if not -(1 << 31) <= cost < (1 << 31):
                raise ValueError(f"dictionary row {line_number} cost exceeds int32: {cost}")

            normalized_key = (reading, surface)
            originals = original_by_normalized.setdefault(normalized_key, set())
            originals.add((reading_raw, surface_raw))
            previous = rows.get(normalized_key)
            if previous is None:
                rows[normalized_key] = (cost, 1)
            else:
                stats.duplicate_pairs += 1
                rows[normalized_key] = (min(previous[0], cost), previous[1] + 1)

    stats.normalization_collisions = sum(
        1 for originals in original_by_normalized.values() if len(originals) > 1
    )
    stats.distinct_pairs = len(rows)
    if rows:
        costs = [value[0] for value in rows.values()]
        stats.min_cost = min(costs)
        stats.max_cost = max(costs)
    return rows


def write_tsv(path: Path, rows: dict[tuple[str, str], tuple[int, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ranked = sorted(rows.items(), key=lambda item: (item[1][0], item[0][0], item[0][1]))
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(["reading", "surface", "cost", "source_rows"])
        for (reading, surface), (cost, source_rows) in ranked:
            writer.writerow([reading, surface, cost, source_rows])


def write_binary(path: Path, rows: dict[tuple[str, str], tuple[int, int]]) -> int:
    hashed: list[tuple[int, int, str, str]] = []
    seen_hashes: dict[int, tuple[str, str]] = {}
    for (reading, surface), (cost, _source_rows) in rows.items():
        hash_value = key_hash(reading, surface)
        previous = seen_hashes.get(hash_value)
        if previous is not None and previous != (reading, surface):
            raise ValueError(
                "FNV-1a collision: "
                f"{previous[0]} -> {previous[1]} and {reading} -> {surface}"
            )
        seen_hashes[hash_value] = (reading, surface)
        hashed.append((hash_value, cost, reading, surface))
    hashed.sort(key=lambda item: item[0])

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        output.write(
            HEADER_STRUCT.pack(
                MAGIC,
                VERSION,
                HASH_ALGORITHM_ID,
                ENTRY_STRUCT.size,
                NORMALIZATION_ID,
                len(hashed),
            )
        )
        for hash_value, cost, _reading, _surface in hashed:
            output.write(ENTRY_STRUCT.pack(hash_value, cost))
    return len(hashed)


def verify_binary(path: Path, expected_entries: int) -> None:
    with path.open("rb") as source:
        header = source.read(HEADER_STRUCT.size)
        if len(header) != HEADER_STRUCT.size:
            raise ValueError("Sudachi cost binary header is truncated")
        magic, version, hash_algorithm, entry_size, normalization, entry_count = (
            HEADER_STRUCT.unpack(header)
        )
        if magic != MAGIC or version != VERSION:
            raise ValueError("Sudachi cost binary header mismatch")
        if hash_algorithm != HASH_ALGORITHM_ID or normalization != NORMALIZATION_ID:
            raise ValueError("Sudachi cost binary algorithm mismatch")
        if entry_size != ENTRY_STRUCT.size:
            raise ValueError("Sudachi cost binary entry size mismatch")
        payload = source.read()
    if entry_count != expected_entries:
        raise ValueError(
            f"Sudachi cost binary count mismatch: expected={expected_entries} actual={entry_count}"
        )
    if len(payload) != entry_count * ENTRY_STRUCT.size:
        raise ValueError("Sudachi cost binary payload size mismatch")
    keys = [
        ENTRY_STRUCT.unpack_from(payload, offset)[0]
        for offset in range(0, len(payload), ENTRY_STRUCT.size)
    ]
    if keys != sorted(keys):
        raise ValueError("Sudachi cost binary hashes are not sorted")


def main() -> None:
    args = parse_args()
    stats = BuildStats()
    rows = load_rows(args.input, stats)
    if not rows:
        raise ValueError("No Sudachi word-cost entries were produced")

    write_tsv(args.output_tsv, rows)
    stats.exported_entries = write_binary(args.output_bin, rows)
    verify_binary(args.output_bin, stats.exported_entries)

    manifest = {
        "format_version": 1,
        "purpose": "Futatsumugi Sudachi reading+surface word-cost ranking evidence",
        "binary_format": {
            "magic_ascii": MAGIC.decode("ascii"),
            "version": VERSION,
            "endianness": "little",
            "header_bytes": HEADER_STRUCT.size,
            "entry_bytes": ENTRY_STRUCT.size,
            "entry_layout": "int64 signed_hash, int32 minimum_cost",
            "hash_algorithm_id": HASH_ALGORITHM_ID,
            "hash_algorithm": "FNV-1a over UTF-16 code units: reading + U+001F + surface",
            "normalization_id": NORMALIZATION_ID,
            "normalization": "Unicode NFKC",
            "collision_policy": "fail build on distinct-key 64-bit hash collision",
            "duplicate_policy": "keep minimum cost for duplicate normalized reading+surface pairs",
        },
        "source": {
            "name": args.source_name,
            "ref": args.source_ref,
            "commit": args.source_commit,
            "input_sha256": sha256_file(args.input),
            "license": "Apache License 2.0 (mozcdic-ut-sudachidict repository)",
        },
        "stats": asdict(stats),
        "outputs": {
            args.output_bin.name: {
                "bytes": args.output_bin.stat().st_size,
                "sha256": sha256_file(args.output_bin),
            },
            args.output_tsv.name: {
                "bytes": args.output_tsv.stat().st_size,
                "sha256": sha256_file(args.output_tsv),
            },
        },
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(asdict(stats), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
