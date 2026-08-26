#!/usr/bin/env python3
"""Patcher de musiques pour Heatwarped.

Format conseillé : NN - Artiste - Titre.ext
01-08 remplacent les pistes du jeu, 09-99 ajoutent des pistes custom.
Les fichiers audio sans numéro sont ajoutés à la fin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import traceback
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

APP_VERSION = "2.0"

# Avec PyInstaller --onefile, __file__ pointe vers le dossier temporaire.
# On garde donc les dossiers du patcher a cote de l EXE.
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = SCRIPT_DIR / "input"
DEFAULT_TRACKS_DIR = SCRIPT_DIR / "tracks"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
DEFAULT_SHAREDASSETS = DEFAULT_INPUT_DIR / "sharedassets0.assets"
DEFAULT_RESOURCES = DEFAULT_INPUT_DIR / "resources.assets"
DEFAULT_TOOLS_DIR = SCRIPT_DIR / "tools"
DEFAULT_CONFIG = SCRIPT_DIR / "config.json"

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
OGGVORBIS2FSB5_URL = (
    "https://github.com/uyjulian/oggvorbis2fsb5/releases/latest/download/"
    "oggvorbis2fsb5-win32.zip"
)

MUSIC_SLOTS = [
    {
        "slot": 1,
        "sample_index": 0,
        "sample_name": "carbon",
        "ui_key": "Carbon",
        "event_path": "event:/Music/Carbon",
    },
    {
        "slot": 2,
        "sample_index": 10,
        "sample_name": "knifegirl",
        "ui_key": "knifegirl",
        "event_path": "event:/Music/knifegirl",
    },
    {
        "slot": 3,
        "sample_index": 29,
        "sample_name": "liberation",
        "ui_key": "Liberation",
        "event_path": "event:/Music/Liberation",
    },
    {
        "slot": 4,
        "sample_index": 45,
        "sample_name": "midnight_stage",
        "ui_key": "MidnightStage",
        "event_path": "event:/Music/MidnightStage",
    },
    {
        "slot": 5,
        "sample_index": 50,
        "sample_name": "sirens",
        "ui_key": "Sirens",
        "event_path": "event:/Music/Sirens",
    },
    {
        "slot": 6,
        "sample_index": 87,
        "sample_name": "black",
        "ui_key": "Black",
        "event_path": "event:/Music/Black",
    },
    {
        "slot": 7,
        "sample_index": 89,
        "sample_name": "nightworld",
        "ui_key": "nightworld",
        "event_path": "event:/Music/nightworld",
    },
    {
        "slot": 8,
        "sample_index": 97,
        "sample_name": "to_the_top",
        "ui_key": "ToTheTop",
        "event_path": "event:/Music/ToTheTop",
    },
    {
        "slot": 9,
        "sample_index": 100,
        "sample_name": "warped",
        "ui_key": "Warped",
        "event_path": "event:/Music/Warped",
    },
]
PATCHABLE_MUSIC_SLOTS = [x for x in MUSIC_SLOTS if x["slot"] != 9]
PROTECTED_MUSIC_SLOT = next(x for x in MUSIC_SLOTS if x["slot"] == 9)
SLOT_BY_NUMBER = {x["slot"]: x for x in PATCHABLE_MUSIC_SLOTS}


# Carbon sert de modèle pour les nouveaux events FMOD.
CARBON_EVENT_GUID = bytes.fromhex("3e7c9e9c6af6bb42a93493edb780550a")
CARBON_TIMELINE_GUID = bytes.fromhex("4a0db6d3ebb53b499d05b6ded3f45ccd")
CARBON_INSTRUMENT_GUID = bytes.fromhex("1082c487f86ca34bbb1779b7e0e9b611")
CARBON_RESOURCE_GUID = bytes.fromhex("0085ba0a2b830f46be105bdccb50cd00")
CARBON_INPUT_BUS_GUID = bytes.fromhex("440e624887fd5c41ad3e5a6793f72675")
CARBON_MASTER_TRACK_GUID = bytes.fromhex("736e46934241d94c80c623982a536d9c")
CARBON_NONMASTER_TRACK_GUID = bytes.fromhex("41ab4673ef855e469879a31b0a7e3c42")
STOCK_FSB_SAMPLE_COUNT = 110
JUKEBOX_PATH_ID = 431


# Fréquences utilisées par le header compact FSB5.
FREQUENCIES = [
    4000,
    8000,
    11000,
    11025,
    16000,
    22050,
    24000,
    32000,
    44100,
    48000,
    96000,
]

SUPPORTED_INPUT_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".oga",
    ".m4a",
    ".aac",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
}

FILENAME_RE = re.compile(
    r"^(?P<slot>\d{1,2})\s*-\s*(?P<artist>.+?)\s*-\s*(?P<title>.+)$"
)


class PatcherError(RuntimeError):
    pass


@dataclass
class MetadataChunk:
    chunk_type: int
    data: bytes


@dataclass
class FsbSample:
    name: str
    frequency_code: int
    channels_flag: int
    sample_count: int
    metadata: list[MetadataChunk]
    data: bytes

    @property
    def frequency(self) -> Optional[int]:
        if 0 <= self.frequency_code < len(FREQUENCIES):
            return FREQUENCIES[self.frequency_code]
        return None

    @property
    def channels(self) -> int:
        return self.channels_flag + 1


@dataclass
class Fsb5:
    version: int
    mode: int
    extra8: bytes
    hash16: bytes
    extra_tail8: bytes
    samples: list[FsbSample]
    raw_name_table: bytes
    original_num_samples: int


@dataclass
class TrackReplacement:
    slot: Optional[int]
    artist: str
    title: str
    source: Path


@dataclass
class CustomEventGraph:
    slot: int
    sample_index: int
    event_guid: bytes
    timeline_guid: bytes
    instrument_guid: bytes
    resource_guid: bytes
    input_bus_guid: bytes
    master_track_guid: bytes
    nonmaster_track_guid: bytes


@dataclass
class TimelineInfo:
    sample_index: int
    resource_guid: bytes
    timeline_guid: bytes
    trigger_length_offsets: list[int]
    end_marker_position_offsets: list[int]
    old_trigger_lengths: list[int]
    old_end_positions: list[int]


def u16(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u64(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def p16(value: int) -> bytes:
    return struct.pack("<H", value)


def p32(value: int) -> bytes:
    return struct.pack("<I", value)


def p64(value: int) -> bytes:
    return struct.pack("<Q", value)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_x16(data: bytes | bytearray, off: int) -> tuple[int, int]:
    """Lit un entier X16 FMOD."""
    if off + 2 > len(data):
        raise PatcherError("Unexpected EOF while reading x16 value")
    low = u16(data, off)
    if low & 0x8000:
        if off + 4 > len(data):
            raise PatcherError("Unexpected EOF while reading extended x16 value")
        high = u16(data, off + 2)
        return (low & 0x7FFF) | (high << 15), off + 4
    return low, off + 2


def write_x16(value: int) -> bytes:
    """Écrit un entier X16 FMOD."""
    if value < 0 or value > 0x7FFFFFFF:
        raise PatcherError(f"x16 value out of range: {value}")
    if value < 0x8000:
        return p16(value)
    low = (value & 0x7FFF) | 0x8000
    high = value >> 15
    if high > 0xFFFF:
        raise PatcherError(f"x16 extended value out of range: {value}")
    return p16(low) + p16(high)


def parse_fsb5(raw: bytes) -> Fsb5:
    if len(raw) < 60 or raw[:4] != b"FSB5":
        raise PatcherError("Not an FSB5 file")

    (
        magic,
        version,
        num_samples,
        sample_headers_size,
        name_table_size,
        data_size,
        mode,
        extra8,
        hash16,
        extra_tail8,
    ) = struct.unpack_from("<4sIIIIII8s16s8s", raw, 0)

    if magic != b"FSB5":
        raise PatcherError("Invalid FSB5 magic")
    if version != 1:
        raise PatcherError(f"Unsupported FSB5 version {version}; expected 1")

    header_size = 60
    sample_headers_end = header_size + sample_headers_size
    name_table_start = sample_headers_end
    data_start = name_table_start + name_table_size
    data_end = data_start + data_size

    if data_end > len(raw):
        raise PatcherError("FSB5 sizes point past end of file")

    pos = header_size
    descriptors: list[tuple[int, int, int, list[MetadataChunk]]] = []
    for i in range(num_samples):
        if pos + 8 > sample_headers_end:
            raise PatcherError(f"FSB5 sample header {i} is truncated")
        sh = u64(raw, pos)
        pos += 8

        has_metadata = sh & 1
        frequency_code = (sh >> 1) & 0xF
        channels_flag = (sh >> 5) & 0x1
        data_offset_units = (sh >> 6) & 0x0FFFFFFF
        sample_count = (sh >> 34) & 0x3FFFFFFF

        chunks: list[MetadataChunk] = []
        next_chunk = bool(has_metadata)
        while next_chunk:
            if pos + 4 > sample_headers_end:
                raise PatcherError(f"FSB5 metadata header for sample {i} is truncated")
            mh = u32(raw, pos)
            pos += 4
            next_chunk = bool(mh & 1)
            chunk_size = (mh >> 1) & 0xFFFFFF
            chunk_type = (mh >> 25) & 0x7F
            if pos + chunk_size > sample_headers_end:
                raise PatcherError(f"FSB5 metadata for sample {i} is truncated")
            chunks.append(MetadataChunk(chunk_type, raw[pos : pos + chunk_size]))
            pos += chunk_size

        descriptors.append(
            (frequency_code, channels_flag, sample_count, chunks, data_offset_units * 16)
        )

    raw_name_table = raw[name_table_start:data_start]
    names = [f"sample_{i:03d}" for i in range(num_samples)]
    if name_table_size >= num_samples * 4:
        try:
            offsets = [u32(raw_name_table, i * 4) for i in range(num_samples)]
            parsed_names: list[str] = []
            for off in offsets:
                if off >= len(raw_name_table):
                    raise ValueError
                end = raw_name_table.find(b"\0", off)
                if end < 0:
                    raise ValueError
                parsed_names.append(raw_name_table[off:end].decode("utf-8", errors="replace"))
            names = parsed_names
        except Exception:

            pass

    samples: list[FsbSample] = []
    offsets = [desc[4] for desc in descriptors]
    for i, desc in enumerate(descriptors):
        frequency_code, channels_flag, sample_count, chunks, rel_data_off = desc
        next_rel = offsets[i + 1] if i + 1 < len(offsets) else data_size
        if not (0 <= rel_data_off <= next_rel <= data_size):
            raise PatcherError(f"Invalid FSB5 data offsets for sample {i}")
        sample_data = raw[data_start + rel_data_off : data_start + next_rel]
        samples.append(
            FsbSample(
                name=names[i],
                frequency_code=frequency_code,
                channels_flag=channels_flag,
                sample_count=sample_count,
                metadata=chunks,
                data=sample_data,
            )
        )

    return Fsb5(
        version=version,
        mode=mode,
        extra8=extra8,
        hash16=hash16,
        extra_tail8=extra_tail8,
        samples=samples,
        raw_name_table=raw_name_table,
        original_num_samples=num_samples,
    )


def build_name_table(fsb: Fsb5) -> bytes:
    """Reconstruit la table des noms FSB5 en gardant le padding d'origine."""
    num_samples = len(fsb.samples)
    original_num_samples = fsb.original_num_samples
    raw = fsb.raw_name_table
    if len(raw) < original_num_samples * 4:
        raise PatcherError("Original FSB5 name table is unexpectedly small")


    

    try:
        original_offsets = [u32(raw, i * 4) for i in range(original_num_samples)]
        original_ends = []
        for off in original_offsets:
            if not (original_num_samples * 4 <= off < len(raw)):
                raise ValueError
            end = raw.find(b"\0", off)
            if end < 0:
                raise ValueError
            original_ends.append(end + 1)
        used_end = max(original_ends, default=original_num_samples * 4)
        trailing_padding = raw[used_end:]
        if any(trailing_padding):

            
            pass
    except Exception as exc:
        raise PatcherError("Could not rebuild FSB5 name table safely") from exc

    table = bytearray(b"\0" * (num_samples * 4))
    cursor = num_samples * 4
    for i, sample in enumerate(fsb.samples):
        encoded = sample.name.encode("utf-8")
        if b"\0" in encoded:
            raise PatcherError(f"Invalid NUL in sample name: {sample.name!r}")
        struct.pack_into("<I", table, i * 4, cursor)
        table.extend(encoded)
        table.append(0)
        cursor += len(encoded) + 1

    table.extend(trailing_padding)
    return bytes(table)


def serialize_fsb5(fsb: Fsb5) -> bytes:
    num_samples = len(fsb.samples)

    name_table = build_name_table(fsb)

    data_blob = bytearray()
    data_offsets: list[int] = []
    for sample in fsb.samples:
        pad = (-len(data_blob)) % 32
        if pad:
            data_blob.extend(b"\0" * pad)
        data_offsets.append(len(data_blob))
        data_blob.extend(sample.data)

    header_blob = bytearray()
    for sample, rel_data_off in zip(fsb.samples, data_offsets):
        if rel_data_off % 32 != 0:
            raise PatcherError("Internal FSB5 32-byte alignment error")
        if sample.sample_count >= (1 << 30):
            raise PatcherError(f"Sample {sample.name} is too long for FSB5 compact header")
        if rel_data_off // 16 >= (1 << 28):
            raise PatcherError("FSB5 audio data offset exceeds compact header range")

        has_meta = 1 if sample.metadata else 0
        sh = (
            has_meta
            | ((sample.frequency_code & 0xF) << 1)
            | ((sample.channels_flag & 0x1) << 5)
            | ((rel_data_off // 16) << 6)
            | ((sample.sample_count & 0x3FFFFFFF) << 34)
        )
        header_blob.extend(p64(sh))

        for j, chunk in enumerate(sample.metadata):
            if len(chunk.data) >= (1 << 24):
                raise PatcherError("FSB5 metadata chunk too large")
            has_next = 1 if j + 1 < len(sample.metadata) else 0
            mh = has_next | (len(chunk.data) << 1) | ((chunk.chunk_type & 0x7F) << 25)
            header_blob.extend(p32(mh))
            header_blob.extend(chunk.data)


    

    # La bank Heatwarped attend un alignement sur 32 octets.
    padding = (-(60 + len(header_blob) + len(name_table))) % 32
    if padding:
        header_blob.extend(b"\0" * padding)

    fixed_header = struct.pack(
        "<4sIIIIII8s16s8s",
        b"FSB5",
        fsb.version,
        num_samples,
        len(header_blob),
        len(name_table),
        len(data_blob),
        fsb.mode,
        fsb.extra8,
        fsb.hash16,
        fsb.extra_tail8,
    )
    return bytes(fixed_header + header_blob + name_table + data_blob)


def find_embedded_fsb(bank: bytes | bytearray) -> tuple[int, int, int]:
    """Retourne l'offset FSB, l'offset SND et la taille du FSB."""
    if len(bank) < 16 or bank[:4] != b"RIFF":
        raise PatcherError("Master.bank is not a RIFF/FMOD bank")

    fsb_off = bank.find(b"FSB5")
    if fsb_off < 0:
        raise PatcherError("No embedded FSB5 found in Master.bank")


    snd_off = bank.rfind(b"SND ", 0, fsb_off)
    if snd_off < 0 or snd_off + 8 > fsb_off:
        raise PatcherError("Could not locate the SND chunk containing the FSB5")
    snd_size = u32(bank, snd_off + 4)
    snd_payload = snd_off + 8
    snd_end = snd_payload + snd_size
    if not (snd_payload <= fsb_off < snd_end <= len(bank)):
        raise PatcherError("SND chunk boundaries are inconsistent")

    old_fsb_size = snd_end - fsb_off
    return fsb_off, snd_off, old_fsb_size


def find_sound_data_header_entry(
    bank: bytes | bytearray, fsb_off: int, search_end: Optional[int] = None
) -> tuple[int, int, int]:
    """Retrouve l'entrée SNDH qui décrit le FSB embarqué."""
    limit = len(bank) if search_end is None else min(search_end, len(bank))
    pos = 0
    candidates: list[tuple[int, int, int]] = []

    while True:
        off = bank.find(b"SNDH", pos, limit)
        if off < 0:
            break
        if off + 8 > limit:
            break
        chunk_size = u32(bank, off + 4)
        ps = off + 8
        pe = ps + chunk_size
        if pe > limit:
            pos = off + 4
            continue

        try:
            raw_count, p = read_x16(bank, ps)
            count = raw_count >> 1
            if count <= 0 or p + 2 > pe:
                pos = off + 4
                continue
            elem_size = u16(bank, p)
            p += 2
            if elem_size < 8:
                pos = off + 4
                continue

            for _ in range(count):
                if p + elem_size > pe:
                    raise PatcherError("Truncated SNDH SoundDataHeader entry")
                entry_fsb_off = u32(bank, p)
                entry_len = u32(bank, p + 4)
                if entry_fsb_off == fsb_off:
                    candidates.append((p, p + 4, entry_len))
                p += elem_size
        except PatcherError:
            raise
        except Exception:
            pass
        pos = off + 4

    if len(candidates) != 1:
        raise PatcherError(
            f"Expected exactly one SNDH entry for FSB at 0x{fsb_off:X}; found {len(candidates)}"
        )
    return candidates[0]


def iter_chunks(data: bytes | bytearray, start: int, end: int) -> Iterable[tuple[int, bytes, int, int]]:
    """Parcourt les chunks RIFF d'une zone donnée."""
    pos = start
    while pos + 8 <= end:
        cid = bytes(data[pos : pos + 4])
        size = u32(data, pos + 4)
        payload_start = pos + 8
        payload_end = payload_start + size
        if payload_end > end:
            return
        yield pos, cid, payload_start, payload_end
        pos = payload_end + (size & 1)


def find_list_payload(bank: bytes | bytearray, list_type: bytes, search_end: int) -> tuple[int, int]:
    """Retrouve le contenu d'une LIST RIFF."""
    pos = 0
    while True:
        off = bank.find(b"LIST", pos, search_end)
        if off < 0:
            break
        if off + 12 <= search_end:
            size = u32(bank, off + 4)
            payload_start = off + 8
            payload_end = payload_start + size
            if payload_end <= search_end and bytes(bank[payload_start : payload_start + 4]) == list_type:
                return payload_start + 4, payload_end
        pos = off + 4
    raise PatcherError(f"Could not locate LIST/{list_type.decode(errors='replace')}")


def find_list_node(
    bank: bytes | bytearray, list_type: bytes, search_end: Optional[int] = None
) -> tuple[int, int, int, int]:
    """Retrouve une LIST RIFF et ses bornes."""
    limit = len(bank) if search_end is None else min(search_end, len(bank))
    pos = 0
    while True:
        off = bank.find(b"LIST", pos, limit)
        if off < 0:
            break
        if off + 12 <= limit:
            size = u32(bank, off + 4)
            ps = off + 8
            pe = ps + size
            if pe <= limit and bytes(bank[ps : ps + 4]) == list_type:
                return off, ps, ps + 4, pe
        pos = off + 4
    raise PatcherError(f"Could not locate LIST/{list_type.decode(errors='replace')}")


def _extract_list_item_containing_guid(
    bank: bytes | bytearray, list_type: bytes, guid: bytes, search_end: int
) -> bytes:
    _, _, children_start, children_end = find_list_node(bank, list_type, search_end)
    matches: list[bytes] = []
    for off, cid, ps, pe in iter_chunks(bank, children_start, children_end):
        if cid == b"LIST" and guid in bank[off:pe]:
            matches.append(bytes(bank[off:pe]))
    if len(matches) != 1:
        raise PatcherError(
            f"Expected exactly one LIST/{list_type.decode()} item containing GUID {guid.hex()}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _extract_wav_item_by_guid(
    bank: bytes | bytearray, resource_guid: bytes, search_end: int
) -> bytes:
    _, _, children_start, children_end = find_list_node(bank, b"WAVS", search_end)
    matches: list[bytes] = []
    for off, cid, ps, pe in iter_chunks(bank, children_start, children_end):
        if cid == b"WAV " and pe - ps >= 30 and bytes(bank[ps : ps + 16]) == resource_guid:
            matches.append(bytes(bank[off:pe]))
    if len(matches) != 1:
        raise PatcherError(
            f"Expected exactly one WAV resource {resource_guid.hex()}, found {len(matches)}"
        )
    return matches[0]


def _replace_exact_guid(blob: bytes, old: bytes, new: bytes, expected: int = 1) -> bytes:
    count = blob.count(old)
    if count != expected:
        raise PatcherError(
            f"Template GUID occurrence mismatch for {old.hex()}: expected {expected}, found {count}"
        )
    return blob.replace(old, new)


def _custom_guid(slot: int, role: str) -> bytes:

    
    # GUID stables : un slot custom garde les mêmes IDs entre deux patchs.
    seed = f"HeatwarpedMusicPatcher/v1/custom-slot/{slot:02d}/{role}".encode("ascii")
    guid = bytearray(hashlib.sha256(seed).digest()[:16])

    
    guid[6] = (guid[6] & 0x0F) | 0x40
    guid[8] = (guid[8] & 0x3F) | 0x80
    return bytes(guid)


def _append_fmod_list_item(bank: bytearray, list_type: bytes, item: bytes) -> None:
    """Ajoute un objet dans une LIST FMOD et recalcule les tailles."""
    if len(item) & 1:
        raise PatcherError("Custom FMOD template item has odd total size; unsupported safely")

    fsb_off = bank.find(b"FSB5")
    if fsb_off < 0:
        raise PatcherError("Embedded FSB disappeared while extending FMOD metadata")
    list_off, _, children_start, list_end = find_list_node(bank, list_type, fsb_off)
    proj_off, _, _, proj_end = find_list_node(bank, b"PROJ", fsb_off)
    if not (proj_off < list_off < proj_end):
        raise PatcherError(f"LIST/{list_type.decode()} is not inside LIST/PROJ")


    if bytes(bank[children_start : children_start + 4]) != b"LCNT":
        raise PatcherError(f"LIST/{list_type.decode()} does not start with LCNT")
    if u32(bank, children_start + 4) != 4:
        raise PatcherError(f"Unexpected LCNT payload size in LIST/{list_type.decode()}")
    count_off = children_start + 8
    old_count = u32(bank, count_off)
    bank[count_off : count_off + 4] = p32(old_count + 1)

    old_list_size = u32(bank, list_off + 4)
    old_proj_size = u32(bank, proj_off + 4)
    bank[list_off + 4 : list_off + 8] = p32(old_list_size + len(item))
    bank[proj_off + 4 : proj_off + 8] = p32(old_proj_size + len(item))
    bank[list_end:list_end] = item
    bank[4:8] = p32(len(bank) - 8)


def _read_single_sndh_entry(bank: bytes | bytearray, search_end: int) -> tuple[int, int, int, int]:
    """Lit l'unique entrée SNDH de la bank Heatwarped."""
    pos = bank.find(b"SNDH", 0, search_end)
    if pos < 0 or pos + 8 > search_end:
        raise PatcherError("Could not locate SNDH after extending FMOD metadata")
    size = u32(bank, pos + 4)
    ps, pe = pos + 8, pos + 8 + size
    if pe > search_end:
        raise PatcherError("Truncated SNDH")
    raw_count, p = read_x16(bank, ps)
    count = raw_count >> 1
    if count != 1 or p + 2 > pe:
        raise PatcherError(f"Expected one SNDH entry, got {count}")
    elem_size = u16(bank, p)
    p += 2
    if elem_size < 8 or p + elem_size > pe:
        raise PatcherError("Unexpected SNDH entry size")
    return p, p + 4, u32(bank, p), u32(bank, p + 4)


def sync_sndh_fsb_offset(bank: bytearray) -> None:
    """Resynchronise l'offset FSB stocké dans SNDH."""
    fsb_off = bank.find(b"FSB5")
    snd_off = bank.rfind(b"SND ", 0, fsb_off)
    if fsb_off < 0 or snd_off < 0:
        raise PatcherError("Could not relocate SND/FSB after FMOD graph insertion")
    off_field, _, _, _ = _read_single_sndh_entry(bank, snd_off)
    if fsb_off > 0xFFFFFFFF:
        raise PatcherError("FSB absolute offset exceeds SNDH uint32 range")
    bank[off_field : off_field + 4] = p32(fsb_off)


def _find_hash_chunk(
    bank: bytes | bytearray, search_end: Optional[int] = None
) -> tuple[int, int, int]:
    """Retrouve le chunk HASH dans PROJ."""
    limit = len(bank) if search_end is None else min(search_end, len(bank))
    proj_off, _, children_start, proj_end = find_list_node(bank, b"PROJ", limit)
    matches: list[tuple[int, int, int]] = []
    for off, cid, ps, pe in iter_chunks(bank, children_start, proj_end):
        if cid == b"HASH":
            matches.append((off, ps, pe))
    if len(matches) != 1:
        raise PatcherError(f"Expected exactly one PROJ/HASH chunk, found {len(matches)}")
    off, ps, pe = matches[0]
    if not (proj_off < off < proj_end):
        raise PatcherError("HASH chunk is not inside LIST/PROJ")
    return off, ps, pe


def _parse_hash_records(
    bank: bytes | bytearray, search_end: Optional[int] = None
) -> tuple[int, int, list[bytes]]:
    """Lit les entrées du HASH FMOD."""
    _, ps, pe = _find_hash_chunk(bank, search_end)
    raw_count, p = read_x16(bank, ps)
    flag = raw_count & 1
    count = raw_count >> 1
    if p + 2 > pe:
        raise PatcherError("HASH payload is truncated before element size")
    elem_size = u16(bank, p)
    p += 2
    if elem_size != 20:
        raise PatcherError(f"Unexpected HASH element size {elem_size}; expected 20")
    expected_end = p + count * elem_size
    if expected_end != pe:
        raise PatcherError(
            f"HASH payload size mismatch: count={count}, elem={elem_size}, "
            f"expected end 0x{expected_end:X}, actual 0x{pe:X}"
        )
    records = [bytes(bank[p + i * elem_size : p + (i + 1) * elem_size]) for i in range(count)]
    guids = [r[:16] for r in records]
    if guids != sorted(guids):
        raise PatcherError("HASH records are not sorted by GUID; refusing to rebuild blindly")
    if len(set(guids)) != len(guids):
        raise PatcherError("HASH contains duplicate GUID entries")
    return flag, elem_size, records


def _extend_hash_for_custom_graphs(
    original_bank: bytes,
    bank: bytearray,
    graphs: dict[int, CustomEventGraph],
) -> None:
    """Ajoute les GUID custom nécessaires dans HASH."""
    if not graphs:
        return

    original_fsb_off = original_bank.find(b"FSB5")
    if original_fsb_off < 0:
        raise PatcherError("Original bank has no FSB5 while rebuilding HASH")
    orig_flag, orig_elem_size, orig_records = _parse_hash_records(original_bank, original_fsb_off)
    if orig_elem_size != 20:
        raise PatcherError("Unsupported original HASH element size")

    def donor_record(guid: bytes, label: str) -> bytes:
        hits = [r for r in orig_records if r[:16] == guid]
        if len(hits) != 1:
            raise PatcherError(
                f"Expected exactly one Carbon {label} HASH record for {guid.hex()}, found {len(hits)}"
            )
        return hits[0]

    carbon_event_hash = donor_record(CARBON_EVENT_GUID, "Event")
    carbon_resource_hash = donor_record(CARBON_RESOURCE_GUID, "WaveformResource")

    current_fsb_off = bank.find(b"FSB5")
    if current_fsb_off < 0:
        raise PatcherError("FSB disappeared before HASH extension")
    cur_flag, elem_size, records = _parse_hash_records(bank, current_fsb_off)
    if cur_flag != orig_flag or elem_size != orig_elem_size:
        raise PatcherError("HASH version/layout changed unexpectedly before extension")

    existing = {r[:16] for r in records}
    additions: list[bytes] = []
    for slot in sorted(graphs):
        graph = graphs[slot]
        for guid, template, label in (
            (graph.event_guid, carbon_event_hash, "Event"),
            (graph.resource_guid, carbon_resource_hash, "WaveformResource"),
        ):
            if guid in existing or any(r[:16] == guid for r in additions):
                raise PatcherError(
                    f"Custom slot {slot:02d} {label} GUID already exists in HASH: {guid.hex()}"
                )
            additions.append(guid + template[16:20])

    new_records = sorted(records + additions, key=lambda r: r[:16])
    new_raw_count = (len(new_records) << 1) | cur_flag
    new_payload = write_x16(new_raw_count) + p16(elem_size) + b"".join(new_records)

    hash_off, ps, pe = _find_hash_chunk(bank, current_fsb_off)
    old_payload_size = pe - ps
    delta = len(new_payload) - old_payload_size
    if delta <= 0:
        raise PatcherError("HASH extension did not increase payload size")
    if delta & 1:
        raise PatcherError("HASH extension produced odd RIFF growth; unsupported safely")

    proj_off, _, _, proj_end = find_list_node(bank, b"PROJ", current_fsb_off)
    if not (proj_off < hash_off < proj_end):
        raise PatcherError("HASH is no longer inside PROJ")

    old_hash_size = u32(bank, hash_off + 4)
    if old_hash_size != old_payload_size:
        raise PatcherError("HASH chunk header/payload size mismatch")
    old_proj_size = u32(bank, proj_off + 4)

    bank[hash_off + 4 : hash_off + 8] = p32(len(new_payload))
    bank[proj_off + 4 : proj_off + 8] = p32(old_proj_size + delta)
    bank[ps:pe] = new_payload
    bank[4:8] = p32(len(bank) - 8)


    
    sync_sndh_fsb_offset(bank)


    
    final_fsb_off = bank.find(b"FSB5")
    final_flag, final_elem_size, final_records = _parse_hash_records(bank, final_fsb_off)
    if final_flag != orig_flag or final_elem_size != elem_size:
        raise PatcherError("HASH changed version/layout after extension")
    expected_count = len(records) + 2 * len(graphs)
    if len(final_records) != expected_count:
        raise PatcherError(
            f"HASH count mismatch after extension: expected {expected_count}, got {len(final_records)}"
        )
    final_map = {r[:16]: r for r in final_records}
    for r in records:
        if final_map.get(r[:16]) != r:
            raise PatcherError(f"Existing HASH record changed for GUID {r[:16].hex()}")
    for slot, graph in graphs.items():
        event_rec = final_map.get(graph.event_guid)
        resource_rec = final_map.get(graph.resource_guid)
        if event_rec is None or event_rec[16:20] != carbon_event_hash[16:20]:
            raise PatcherError(f"Custom slot {slot:02d} Event HASH registration failed")
        if resource_rec is None or resource_rec[16:20] != carbon_resource_hash[16:20]:
            raise PatcherError(f"Custom slot {slot:02d} WaveformResource HASH registration failed")


def _validate_custom_graph_topology(
    bank: bytes | bytearray,
    graphs: dict[int, CustomEventGraph],
) -> None:
    """Vérifie les références GUID des events custom."""
    if not graphs:
        return
    fsb_off = bank.find(b"FSB5")
    if fsb_off < 0:
        raise PatcherError("FSB missing during custom graph topology validation")

    expected = {
        "event_guid": 2,          
        "timeline_guid": 3,       
        "instrument_guid": 2,     
        "resource_guid": 3,       
        "input_bus_guid": 2,      
        "master_track_guid": 3,   
        "nonmaster_track_guid": 3,
    }
    metadata = bytes(bank[:fsb_off])
    for slot, graph in sorted(graphs.items()):
        for field, want in expected.items():
            guid = getattr(graph, field)
            got = metadata.count(guid)
            if got != want:
                raise PatcherError(
                    f"Custom slot {slot:02d} topology mismatch for {field}: expected {want} "
                    f"metadata references, found {got}"
                )


def _set_master_bus_item_gain(item: bytes, gain_db: float = 0.0) -> bytes:
    data = bytearray(item)
    bus_chunks = []
    for _, cid, ps, pe in iter_chunks(data, 12, len(data)):
        if cid == b"BUS ":
            bus_chunks.append((ps, pe))

    if len(bus_chunks) != 1:
        raise PatcherError(f"Expected one BUS chunk in Master Bus, found {len(bus_chunks)}")

    ps, pe = bus_chunks[0]
    if pe - ps < 18:
        raise PatcherError("Master Bus BUS chunk is too small")

    struct.pack_into("<f", data, ps + 14, float(gain_db))
    return bytes(data)


def _master_track_guid_for_timeline(
    bank: bytes | bytearray, timeline_guid: bytes, search_end: int
) -> bytes:
    event = _extract_list_item_containing_guid(bank, b"EVTS", timeline_guid, search_end)
    for _, cid, ps, pe in iter_chunks(event, 12, len(event)):
        if cid != b"EVTB":
            continue
        if pe - ps < 80:
            raise PatcherError("EVTB chunk is too small to read its Master Track")
        if bytes(event[ps + 32 : ps + 48]) != timeline_guid:
            continue
        return bytes(event[ps + 64 : ps + 80])
    raise PatcherError("Could not find the Master Track for this music event")


def set_music_event_gain_db(
    bank: bytearray, timeline_guid: bytes, gain_db: float = 0.0
) -> None:
    fsb_off = bank.find(b"FSB5")
    if fsb_off < 0:
        raise PatcherError("FSB missing while patching music gain")

    master_guid = _master_track_guid_for_timeline(bank, timeline_guid, fsb_off)
    _, _, children_start, children_end = find_list_node(bank, b"MBSS", fsb_off)
    matches = []

    for off, cid, ps, pe in iter_chunks(bank, children_start, children_end):
        if cid != b"LIST" or master_guid not in bank[off:pe]:
            continue
        for _, child_id, cps, cpe in iter_chunks(bank, ps + 4, pe):
            if child_id == b"BUS ":
                matches.append((cps, cpe))

    if len(matches) != 1:
        raise PatcherError(
            f"Expected one Master Bus for GUID {master_guid.hex()}, found {len(matches)}"
        )

    ps, pe = matches[0]
    if pe - ps < 18:
        raise PatcherError("Master Bus BUS chunk is too small")
    struct.pack_into("<f", bank, ps + 14, float(gain_db))


def clone_custom_music_graphs(
    original_bank: bytes,
    bank: bytearray,
    custom_samples: list[tuple[int, int]],
) -> dict[int, CustomEventGraph]:
    """Clone le graph FMOD de Carbon pour les pistes custom."""
    if not custom_samples:
        return {}

    original_fsb_off = original_bank.find(b"FSB5")
    if original_fsb_off < 0:
        raise PatcherError("Original bank has no embedded FSB5")

    event_template = _extract_list_item_containing_guid(
        original_bank, b"EVTS", CARBON_EVENT_GUID, original_fsb_off
    )
    timeline_template = _extract_list_item_containing_guid(
        original_bank, b"TLNS", CARBON_TIMELINE_GUID, original_fsb_off
    )
    wait_template = _extract_list_item_containing_guid(
        original_bank, b"WAIS", CARBON_RESOURCE_GUID, original_fsb_off
    )
    wav_template = _extract_wav_item_by_guid(
        original_bank, CARBON_RESOURCE_GUID, original_fsb_off
    )
    input_bus_template = _extract_list_item_containing_guid(
        original_bank, b"IBSS", CARBON_INPUT_BUS_GUID, original_fsb_off
    )
    group_bus_template = _extract_list_item_containing_guid(
        original_bank, b"GBSS", CARBON_NONMASTER_TRACK_GUID, original_fsb_off
    )
    master_bus_template = _extract_list_item_containing_guid(
        original_bank, b"MBSS", CARBON_MASTER_TRACK_GUID, original_fsb_off
    )

    graphs: dict[int, CustomEventGraph] = {}
    occupied = bytes(bank[: bank.find(b"FSB5")])
    for slot, sample_index in custom_samples:
        graph = CustomEventGraph(
            slot=slot,
            sample_index=sample_index,
            event_guid=_custom_guid(slot, "event"),
            timeline_guid=_custom_guid(slot, "timeline"),
            instrument_guid=_custom_guid(slot, "instrument"),
            resource_guid=_custom_guid(slot, "resource"),
            input_bus_guid=_custom_guid(slot, "input-bus"),
            master_track_guid=_custom_guid(slot, "master-track"),
            nonmaster_track_guid=_custom_guid(slot, "nonmaster-track"),
        )
        for value in (
            graph.event_guid,
            graph.timeline_guid,
            graph.instrument_guid,
            graph.resource_guid,
            graph.input_bus_guid,
            graph.master_track_guid,
            graph.nonmaster_track_guid,
        ):
            if value in occupied:
                raise PatcherError(f"Generated custom GUID collision for slot {slot:02d}: {value.hex()}")

        ev = _replace_exact_guid(event_template, CARBON_EVENT_GUID, graph.event_guid)
        ev = _replace_exact_guid(ev, CARBON_TIMELINE_GUID, graph.timeline_guid)
        ev = _replace_exact_guid(ev, CARBON_INPUT_BUS_GUID, graph.input_bus_guid)
        ev = _replace_exact_guid(ev, CARBON_MASTER_TRACK_GUID, graph.master_track_guid)
        ev = _replace_exact_guid(ev, CARBON_NONMASTER_TRACK_GUID, graph.nonmaster_track_guid)

        ibus = _replace_exact_guid(input_bus_template, CARBON_INPUT_BUS_GUID, graph.input_bus_guid)
        mbus = _replace_exact_guid(master_bus_template, CARBON_MASTER_TRACK_GUID, graph.master_track_guid)
        mbus = _set_master_bus_item_gain(mbus, 0.0)
        gbus = _replace_exact_guid(group_bus_template, CARBON_NONMASTER_TRACK_GUID, graph.nonmaster_track_guid)
        gbus = _replace_exact_guid(gbus, CARBON_MASTER_TRACK_GUID, graph.master_track_guid)

        tl = _replace_exact_guid(timeline_template, CARBON_TIMELINE_GUID, graph.timeline_guid)
        tl = _replace_exact_guid(tl, CARBON_INSTRUMENT_GUID, graph.instrument_guid)

        wi = _replace_exact_guid(wait_template, CARBON_INSTRUMENT_GUID, graph.instrument_guid)
        wi = _replace_exact_guid(wi, CARBON_RESOURCE_GUID, graph.resource_guid)
        wi = _replace_exact_guid(wi, CARBON_TIMELINE_GUID, graph.timeline_guid)

        

        # Le routing doit pointer vers le mixer cloné, sinon la piste reste muette.
        wi = _replace_exact_guid(wi, CARBON_NONMASTER_TRACK_GUID, graph.nonmaster_track_guid)

        wav = _replace_exact_guid(wav_template, CARBON_RESOURCE_GUID, graph.resource_guid)

        
        if len(wav) != 38 or bytes(wav[:4]) != b"WAV ":
            raise PatcherError("Unexpected Carbon WAV template size/layout")
        wav_mut = bytearray(wav)
        struct.pack_into("<i", wav_mut, 8 + 22, sample_index)
        wav = bytes(wav_mut)


        
        _append_fmod_list_item(bank, b"IBSS", ibus)
        _append_fmod_list_item(bank, b"GBSS", gbus)
        _append_fmod_list_item(bank, b"MBSS", mbus)
        _append_fmod_list_item(bank, b"EVTS", ev)
        _append_fmod_list_item(bank, b"TLNS", tl)
        _append_fmod_list_item(bank, b"WAIS", wi)
        _append_fmod_list_item(bank, b"WAVS", wav)
        graphs[slot] = graph
        occupied += b"".join(
            (
                graph.event_guid,
                graph.timeline_guid,
                graph.instrument_guid,
                graph.resource_guid,
                graph.input_bus_guid,
                graph.master_track_guid,
                graph.nonmaster_track_guid,
            )
        )


    
    _extend_hash_for_custom_graphs(original_bank, bank, graphs)


    sync_sndh_fsb_offset(bank)
    _validate_custom_graph_topology(bank, graphs)
    return graphs


def discover_timeline_for_sample(
    bank: bytes | bytearray, fsb_off: int, sample_index: int
) -> TimelineInfo:
    resources = parse_waveform_resources(bank, fsb_off)
    if sample_index not in resources:
        raise PatcherError(f"No WaveformResource found for FSB sample index {sample_index}")
    resource_guid = resources[sample_index]
    timeline_guid = find_timeline_guid_for_resource(bank, fsb_off, resource_guid)
    return parse_timeline_offsets(bank, fsb_off, timeline_guid, sample_index, resource_guid)


def parse_waveform_resources(bank: bytes | bytearray, fsb_off: int) -> dict[int, bytes]:
    children_start, children_end = find_list_payload(bank, b"WAVS", fsb_off)
    out: dict[int, bytes] = {}
    for _, cid, ps, pe in iter_chunks(bank, children_start, children_end):
        if cid != b"WAV " or pe - ps < 30:
            continue

        guid = bytes(bank[ps : ps + 16])

        
        bank_index = u32(bank, ps + 18)
        subsound_index = u32(bank, ps + 22)
        if bank_index == 0:
            out[subsound_index] = guid
    return out


def find_timeline_guid_for_resource(
    bank: bytes | bytearray, fsb_off: int, resource_guid: bytes
) -> bytes:
    children_start, children_end = find_list_payload(bank, b"WAIS", fsb_off)

    for list_off, cid, ps, pe in iter_chunks(bank, children_start, children_end):
        if cid != b"LIST" or pe - ps < 4:
            continue
        list_type = bytes(bank[ps : ps + 4])
        if list_type != b"WAIT":
            continue
        item_start = ps + 4
        waib_resource_match = False
        timeline_guid: Optional[bytes] = None
        for _, child_id, cps, cpe in iter_chunks(bank, item_start, pe):
            if child_id == b"WAIB" and cpe - cps >= 32:

                if bytes(bank[cps + 16 : cps + 32]) == resource_guid:
                    waib_resource_match = True
            elif child_id == b"INST" and cpe - cps >= 16:

                timeline_guid = bytes(bank[cps : cps + 16])
        if waib_resource_match and timeline_guid is not None:
            return timeline_guid

    raise PatcherError("Could not map WaveformResource GUID to a Timeline GUID")


def parse_timeline_offsets(
    bank: bytes | bytearray,
    fsb_off: int,
    timeline_guid: bytes,
    sample_index: int,
    resource_guid: bytes,
) -> TimelineInfo:
    pos = 0
    while True:
        off = bank.find(b"TLNB", pos, fsb_off)
        if off < 0:
            break
        if off + 8 > fsb_off:
            break
        size = u32(bank, off + 4)
        ps = off + 8
        pe = ps + size
        if pe <= fsb_off and pe - ps >= 16 and bytes(bank[ps : ps + 16]) == timeline_guid:
            p = ps + 16


            
            raw_count, p = read_x16(bank, p)
            count = raw_count >> 1
            if count:
                if p + 2 > pe:
                    raise PatcherError("Truncated Timeline TriggerBox list")
                payload_size = u16(bank, p)
                p += 2
                if payload_size < 24:
                    raise PatcherError("Unexpected TriggerBox payload size")
                p += count * payload_size
                if p > pe:
                    raise PatcherError("Truncated Timeline TriggerBox list")


            raw_tl_count, p = read_x16(bank, p)
            tl_count = raw_tl_count >> 1
            trigger_length_offsets: list[int] = []
            old_trigger_lengths: list[int] = []
            if tl_count:
                if p + 2 > pe:
                    raise PatcherError("Truncated Timeline TimeLockedTriggerBox list")
                payload_size = u16(bank, p)
                p += 2
                if payload_size < 24:
                    raise PatcherError("Unexpected TimeLockedTriggerBox payload size")
                for _ in range(tl_count):
                    if p + payload_size > pe:
                        raise PatcherError("Truncated TimeLockedTriggerBox")
                    length_off = p + 20  
                    trigger_length_offsets.append(length_off)
                    old_trigger_lengths.append(u32(bank, length_off))
                    p += payload_size


            raw_sustain_count, p = read_x16(bank, p)
            sustain_count = raw_sustain_count >> 1
            for _ in range(sustain_count):
                if p + 2 > pe:
                    raise PatcherError("Truncated SustainPoint list")
                elem_size = u16(bank, p)
                p += 2
                if p + elem_size > pe:
                    raise PatcherError("Truncated SustainPoint")
                p += elem_size


            

            raw_marker_count, p = read_x16(bank, p)
            marker_count = raw_marker_count >> 1
            end_offsets: list[int] = []
            old_end_positions: list[int] = []
            for _ in range(marker_count):
                if p + 2 > pe:
                    raise PatcherError("Truncated NamedMarker list")
                elem_size = u16(bank, p)
                p += 2
                elem_start = p
                elem_end = p + elem_size
                if elem_end > pe or elem_size < 21:
                    raise PatcherError("Unexpected/truncated NamedMarker")

                position_off = elem_start + 16
                name_len, name_pos = read_x16(bank, elem_start + 20)
                if name_pos + name_len > elem_end:
                    raise PatcherError("Truncated NamedMarker name")
                name = bytes(bank[name_pos : name_pos + name_len]).decode("utf-8", errors="replace")
                if name.casefold() == "end":
                    end_offsets.append(position_off)
                    old_end_positions.append(u32(bank, position_off))
                p = elem_end

            return TimelineInfo(
                sample_index=sample_index,
                resource_guid=resource_guid,
                timeline_guid=timeline_guid,
                trigger_length_offsets=trigger_length_offsets,
                end_marker_position_offsets=end_offsets,
                old_trigger_lengths=old_trigger_lengths,
                old_end_positions=old_end_positions,
            )
        pos = off + 4

    raise PatcherError("Could not locate matching TLNB Timeline node")

def discover_music_timelines(bank: bytes | bytearray, fsb_off: int) -> dict[int, TimelineInfo]:
    resources = parse_waveform_resources(bank, fsb_off)
    out: dict[int, TimelineInfo] = {}
    for slot_info in MUSIC_SLOTS:
        idx = slot_info["sample_index"]
        if idx not in resources:
            raise PatcherError(f"No WaveformResource found for FSB sample index {idx}")
        resource_guid = resources[idx]
        timeline_guid = find_timeline_guid_for_resource(bank, fsb_off, resource_guid)
        out[idx] = parse_timeline_offsets(
            bank, fsb_off, timeline_guid, idx, resource_guid
        )
    return out


# Quelques sons non-musicaux servent à vérifier qu'on patch bien la bonne bank.
BANK_LAYOUT_ANCHORS = {
    1: "thunder4",
    15: "tower_hillz",
    24: "roof_outside_close",
    34: "click",
    46: "UI_ACCEPT",
    57: "fountain",
    66: "UI_PREV",
    76: "nos_start",
    83: "option_select",
    98: "nos_end",
    101: "bicycle_6",
    109: "temp_rev_6",
}


def validate_heatwarped_bank(fsb: Fsb5, allow_extra: bool = False) -> None:
    if fsb.mode != 15:
        raise PatcherError(f"Expected FMOD Vorbis FSB mode 15, got {fsb.mode}")
    if len(fsb.samples) < STOCK_FSB_SAMPLE_COUNT:
        raise PatcherError(
            f"This patcher targets the analysed Heatwarped bank with at least "
            f"{STOCK_FSB_SAMPLE_COUNT} stock samples; got {len(fsb.samples)}"
        )
    if not allow_extra and len(fsb.samples) != STOCK_FSB_SAMPLE_COUNT:
        raise PatcherError(
            f"Input Master.bank must be the clean {STOCK_FSB_SAMPLE_COUNT}-sample Heatwarped bank. "
            f"Got {len(fsb.samples)} samples. Rebuild custom tracks from the original input bank."
        )


    

    for idx, expected in BANK_LAYOUT_ANCHORS.items():
        actual = fsb.samples[idx].name
        if actual.casefold() != expected.casefold():
            raise PatcherError(
                f"Bank layout mismatch at FSB #{idx}: expected anchor '{expected}', "
                f"found '{actual}'. Refusing to patch a different bank revision blindly."
            )


    protected_idx = PROTECTED_MUSIC_SLOT["sample_index"]
    protected_expected = PROTECTED_MUSIC_SLOT["sample_name"]
    protected_actual = fsb.samples[protected_idx].name
    if protected_actual.casefold() != protected_expected.casefold():
        raise PatcherError(
            f"Protected WARPED slot mismatch at FSB #{protected_idx}: expected "
            f"'{protected_expected}', found '{protected_actual}'. Refusing to continue."
        )


def _clean_name_parts(parts: list[str]) -> list[str]:
    # On vire les tags de version peu importe leur position.
    return [
        part.strip() for part in parts
        if part.strip()
        and "remaster" not in part.casefold()
        and "remix" not in part.casefold()
    ]


def _parse_artist_title(parts: list[str], permissive: bool = False) -> tuple[str, str]:
    cleaned = _clean_name_parts(parts)

    # Pour un nom non numéroté, plus de 2 champs = ambigu : on ne devine pas l'artiste.
    if permissive and len(cleaned) > 2:
        return "", " - ".join(cleaned)

    if len(cleaned) >= 2:
        return cleaned[0], " - ".join(cleaned[1:])
    if cleaned:
        return "", cleaned[0]
    return "", ""


def parse_track_filename(path: Path) -> Optional[TrackReplacement]:
    if path.suffix.lower() not in SUPPORTED_INPUT_EXTENSIONS:
        return None

    name = path.stem.strip()
    m = FILENAME_RE.match(name)
    if m:
        slot = int(m.group("slot"))
        parts = [m.group("artist"), *m.group("title").split(" - ")]
        artist, title = _parse_artist_title(parts)
        if 1 <= slot <= 99 and title:
            return TrackReplacement(slot, artist, title, path)

    # Pas de nomenclature exploitable : on accepte quand même le fichier en custom AUTO.
    parts = [part.strip() for part in name.split(" - ") if part.strip()]
    if parts and parts[0].isdigit():
        parts = parts[1:]

    artist, title = _parse_artist_title(parts, permissive=True)
    if not title:
        title = name or path.name

    return TrackReplacement(None, artist, title, path)


def scan_tracks(tracks_dir: Path) -> list[TrackReplacement]:
    if not tracks_dir.exists():
        tracks_dir.mkdir(parents=True, exist_ok=True)

    tracks: list[TrackReplacement] = []
    for path in sorted(tracks_dir.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.name.startswith("."):
            continue
        repl = parse_track_filename(path)
        if repl is not None:
            tracks.append(repl)
    return tracks


def resolve_track_layout(
    input_replacements: list[TrackReplacement],
) -> tuple[dict[int, TrackReplacement], dict[int, TrackReplacement], dict[int, TrackReplacement], list[dict]]:
    """Sépare les remplacements stock des pistes custom sans rejeter les doublons."""
    stock: dict[int, TrackReplacement] = {}
    duplicate_stock: list[TrackReplacement] = []
    numbered_custom: list[TrackReplacement] = []
    unnumbered: list[TrackReplacement] = []

    for repl in input_replacements:
        if repl.slot is None:
            unnumbered.append(repl)
        elif 1 <= repl.slot <= 8:
            if repl.slot not in stock:
                stock[repl.slot] = repl
            else:
                duplicate_stock.append(repl)
        else:
            numbered_custom.append(repl)

    # Les doublons 01-08 deviennent les premiers customs.
    # Ensuite viennent les 09-99 dans l'ordre numérique, puis les AUTO.
    duplicate_stock.sort(key=lambda r: (r.slot or 0, r.source.name.casefold()))
    numbered_custom.sort(key=lambda r: (r.slot or 0, r.source.name.casefold()))
    unnumbered.sort(key=lambda r: r.source.name.casefold())
    custom_order = duplicate_stock + numbered_custom + unnumbered

    custom: dict[int, TrackReplacement] = {}
    input_to_internal: list[dict] = []
    for internal_slot, repl in enumerate(custom_order, start=10):
        custom[internal_slot] = repl
        input_to_internal.append({
            "input_slot": repl.slot,
            "internal_slot": internal_slot,
            "source": repl.source.name,
            "stock_duplicate": repl in duplicate_stock,
        })

    resolved = dict(stock)
    resolved.update(custom)
    return resolved, stock, custom, input_to_internal


def _config_bool(value, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise PatcherError(f"config {key} must be true or false")


def _config_float(value, key: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PatcherError(f"config {key} must be a number") from exc
    if not math.isfinite(number):
        raise PatcherError(f"config {key} must be a finite number")
    return number


def load_config(path: Path) -> dict:
    defaults = {
        "vorbis_quality": 6,
        "end_marker_policy": "full",
        "timeline_padding_ms": 0,
        "playlist_mode": "full",
        "normalization_mode": "lufs",
        "target_lufs": -9.0,
        "true_peak": -1.0,
        "target_peak_dbfs": 0.0,
        "fetch_metadata": False,
    }
    if not path.exists():
        return defaults
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PatcherError(f"Invalid config.json: {exc}") from exc
    defaults.update(user)
    policy = str(defaults.get("end_marker_policy", "full")).lower()
    if policy not in {"full", "preserve_original", "preserve_tail"}:
        raise PatcherError(
            "config end_marker_policy must be: full, preserve_original, or preserve_tail"
        )
    defaults["end_marker_policy"] = policy

    # Ancien config : stock reste stock, partial/full deviennent le nouveau full.
    if "playlist_mode" in user:
        raw_playlist_mode = user["playlist_mode"]
    elif "free_roam_playlist" in user:
        legacy_mode = str(user["free_roam_playlist"]).lower().strip()
        raw_playlist_mode = "stock" if legacy_mode in {"stock", "none", "off", "disabled", "native"} else "full"
    else:
        raw_playlist_mode = "full"

    playlist_mode = str(raw_playlist_mode).lower().strip()
    playlist_aliases = {
        "none": "stock",
        "off": "stock",
        "disabled": "stock",
        "native": "stock",
        "all": "full",
        "unlocked": "full",
    }
    playlist_mode = playlist_aliases.get(playlist_mode, playlist_mode)
    if playlist_mode not in {"stock", "full"}:
        raise PatcherError("config playlist_mode must be: stock or full")
    defaults["playlist_mode"] = playlist_mode

    # Nouveau système : LUFS ou peak. L'ancien normalize_tracks reste accepté
    # pour ne pas casser un ancien config.
    if "normalization_mode" in user:
        raw_normalization_mode = user["normalization_mode"]
    elif "normalize_tracks" in user:
        raw_normalization_mode = "peak" if _config_bool(user["normalize_tracks"], "normalize_tracks") else "off"
    else:
        raw_normalization_mode = "lufs"

    normalization_mode = str(raw_normalization_mode).lower().strip()
    normalization_aliases = {
        "loudness": "lufs",
        "dbfs": "peak",
        "none": "off",
        "disabled": "off",
        "false": "off",
    }
    normalization_mode = normalization_aliases.get(normalization_mode, normalization_mode)
    if normalization_mode not in {"lufs", "peak", "off"}:
        raise PatcherError("config normalization_mode must be: lufs, peak, or off")
    defaults["normalization_mode"] = normalization_mode

    target_lufs = -9.0
    true_peak = -1.0
    target_peak_dbfs = 0.0

    if normalization_mode == "lufs":
        target_lufs = _config_float(defaults.get("target_lufs", -9.0), "target_lufs")
        true_peak = _config_float(defaults.get("true_peak", -1.0), "true_peak")
        if not -70.0 <= target_lufs <= -5.0:
            raise PatcherError("config target_lufs must be between -70 and -5")
        if not -9.0 <= true_peak <= 0.0:
            raise PatcherError("config true_peak must be between -9 and 0")
    elif normalization_mode == "peak":
        target_peak_dbfs = _config_float(
            defaults.get("target_peak_dbfs", 0.0), "target_peak_dbfs"
        )
        if not -60.0 <= target_peak_dbfs <= 0.0:
            raise PatcherError("config target_peak_dbfs must be between -60 and 0")

    defaults["target_lufs"] = target_lufs
    defaults["true_peak"] = true_peak
    defaults["target_peak_dbfs"] = target_peak_dbfs
    defaults["fetch_metadata"] = _config_bool(defaults.get("fetch_metadata", False), "fetch_metadata")
    defaults.pop("normalize_tracks", None)
    defaults.pop("free_roam_playlist", None)
    return defaults


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "HeatwarpedMusicPatcher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, destination.open("wb") as f:
            total = int(r.headers.get("Content-Length", "0") or 0)
            done = 0
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r    {done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MiB", end="", flush=True)
            if total:
                print()
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise PatcherError(f"Download failed: {url}\n{exc}") from exc


def extract_zip(zip_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(destination)
    except Exception as exc:
        raise PatcherError(f"Could not extract {zip_path.name}: {exc}") from exc


def find_executable(root: Path, names: set[str]) -> Optional[Path]:
    lowered = {n.casefold() for n in names}
    if not root.exists():
        return None
    for p in root.rglob("*"):
        if p.is_file() and p.name.casefold() in lowered:
            return p
    return None


def install_ffmpeg(tools_dir: Path) -> Path:
    print("Downloading ffmpeg...")
    archive = tools_dir / "_downloads" / "ffmpeg-release-essentials.zip"
    download(FFMPEG_URL, archive)
    extract_zip(archive, tools_dir / "ffmpeg")
    archive.unlink(missing_ok=True)
    exe = find_executable(tools_dir / "ffmpeg", {"ffmpeg.exe", "ffmpeg"})
    if not exe:
        raise PatcherError("ffmpeg was downloaded but ffmpeg.exe was not found")
    return exe


def install_oggvorbis2fsb5(tools_dir: Path) -> Path:
    print("Downloading oggvorbis2fsb5...")
    archive = tools_dir / "_downloads" / "oggvorbis2fsb5-win32.zip"
    download(OGGVORBIS2FSB5_URL, archive)
    extract_zip(archive, tools_dir / "oggvorbis2fsb5")
    archive.unlink(missing_ok=True)
    exe = find_executable(
        tools_dir / "oggvorbis2fsb5",
        {"oggvorbis2fsb5.exe", "oggvorbis2fsb5"},
    )
    if not exe:
        raise PatcherError("oggvorbis2fsb5 was downloaded but the executable was not found")
    return exe


def check_tools(tools_dir: Path) -> tuple[Path, Path]:
    # Les tools sont verifies avant les inputs pour eviter de lancer le patch pour rien.
    tools_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = find_executable(tools_dir / "ffmpeg", {"ffmpeg.exe", "ffmpeg"})
    ogg_tool = find_executable(
        tools_dir / "oggvorbis2fsb5",
        {"oggvorbis2fsb5.exe", "oggvorbis2fsb5"},
    )

    missing = []
    if ffmpeg is None:
        missing.append("ffmpeg")
    if ogg_tool is None:
        missing.append("oggvorbis2fsb5")

    if not missing:
        print("Tools: OK")
        return ffmpeg, ogg_tool

    print("Missing tools: " + ", ".join(missing))
    while True:
        try:
            answer = input("Download missing tools now? (y/n): ").strip().lower()
        except EOFError:
            answer = "n"

        if answer in {"y", "yes"}:
            break
        if answer in {"n", "no"}:
            raise PatcherError(
                "The required tools are missing.\n"
                "Download them first and keep this folder structure:\n"
                "  tools\\ffmpeg\\...\\ffmpeg.exe\n"
                "  tools\\oggvorbis2fsb5\\oggvorbis2fsb5.exe\n"
                "Then run the patcher again."
            )
        print("Please answer y or n.")

    if ffmpeg is None:
        ffmpeg = install_ffmpeg(tools_dir)
    if ogg_tool is None:
        ogg_tool = install_oggvorbis2fsb5(tools_dir)

    print("Tools: OK")
    return ffmpeg, ogg_tool


def run_command(args: list[str], cwd: Optional[Path] = None) -> str:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise PatcherError(
            "External tool failed:\n"
            + " ".join(args)
            + "\n\n"
            + result.stdout[-5000:]
        )
    return result.stdout


def _unescape_ffmetadata(value: str) -> str:
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            i += 1
            escaped = value[i]
            out.append("\n" if escaped == "n" else escaped)
        else:
            out.append(value[i])
        i += 1
    return "".join(out).strip()


def fetch_audio_metadata(source: Path, ffmpeg: Path) -> Optional[tuple[str, str]]:
    # FFmpeg sait lire les tags des formats supportés, pas besoin d'une dépendance Python en plus.
    try:
        output = run_command([
            str(ffmpeg),
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(source),
            "-f", "ffmetadata",
            "-",
        ])
    except PatcherError:
        return None

    tags: dict[str, str] = {}
    for line in output.splitlines():
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().casefold()
        if key in {"artist", "title"} and key not in tags:
            tags[key] = _unescape_ffmetadata(value)

    artist = tags.get("artist", "").strip()
    title = tags.get("title", "").strip()
    return (artist, title) if artist or title else None


def _same_display_text(a: str, b: str) -> bool:
    # Comparaison souple pour éviter Artist == Title à cause de casse/espaces/tirets.
    def key(value: str) -> str:
        return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)

    ka = key(a)
    kb = key(b)
    return bool(ka and kb and ka == kb)


def merge_track_metadata(repl: TrackReplacement, metadata: Optional[tuple[str, str]]) -> None:
    if metadata is None:
        if _same_display_text(repl.artist, repl.title):
            repl.artist = ""
        return

    meta_artist, meta_title = (part.strip() for part in metadata)
    file_artist = repl.artist.strip()
    file_title = repl.title.strip()

    if meta_artist and meta_title:
        artist, title = meta_artist, meta_title
    elif meta_artist:
        # Si le filename est inversé, on prend le champ différent de l'artiste connu.
        candidates = [file_title, file_artist]
        title = next((x for x in candidates if x and not _same_display_text(x, meta_artist)), "")
        if title:
            artist = meta_artist
        else:
            # Pas de titre fiable : on affiche la seule info connue une seule fois.
            artist, title = "", meta_artist
    elif meta_title:
        # Même logique dans l'autre sens : on cherche un artiste différent du titre connu.
        candidates = [file_artist, file_title]
        artist = next((x for x in candidates if x and not _same_display_text(x, meta_title)), "")
        title = meta_title
    else:
        artist, title = file_artist, file_title

    if not title:
        title = file_title or file_artist or repl.source.stem.strip() or repl.source.name

    if _same_display_text(artist, title):
        artist = ""

    repl.artist = artist
    repl.title = title


def measure_peak_db(source: Path, ffmpeg: Path) -> Optional[float]:
    output = run_command(
        [
            str(ffmpeg),
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-af",
            "aformat=sample_rates=48000:channel_layouts=stereo,astats=metadata=0:reset=0",
            "-f",
            "null",
            "-",
        ]
    )

    peaks = []
    for match in re.finditer(r"Peak level dB:\s*(-?inf|[-+]?\d+(?:\.\d+)?)", output, re.IGNORECASE):
        value = match.group(1).lower()
        if value == "-inf":
            continue
        peaks.append(float(value))

    return max(peaks) if peaks else None


def measure_loudnorm(
    source: Path,
    ffmpeg: Path,
    target_lufs: float,
    true_peak: float,
) -> Optional[dict[str, float]]:
    # Première passe loudnorm pour une normalisation LUFS reproductible.
    output = run_command(
        [
            str(ffmpeg),
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-af",
            (
                "aformat=sample_rates=48000:channel_layouts=stereo,"
                f"loudnorm=I={target_lufs:.3f}:TP={true_peak:.3f}:LRA=11:print_format=json"
            ),
            "-f",
            "null",
            "-",
        ]
    )

    matches = re.findall(r'\{\s*"input_i".*?\}', output, flags=re.DOTALL)
    if not matches:
        return None

    try:
        raw = json.loads(matches[-1])
        values = {
            "input_i": float(raw["input_i"]),
            "input_tp": float(raw["input_tp"]),
            "input_lra": float(raw["input_lra"]),
            "input_thresh": float(raw["input_thresh"]),
            "target_offset": float(raw["target_offset"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    if not all(math.isfinite(value) for value in values.values()):
        return None
    return values


def build_donor_fsb(
    repl: TrackReplacement,
    ffmpeg: Path,
    ogg_tool: Path,
    quality: int,
    temp_dir: Path,
    normalization_mode: str,
    target_lufs: float,
    true_peak: float,
    target_peak_dbfs: float,
) -> FsbSample:
    # Les pistes AUTO n'ont pas de numero d'entree.
    # On utilise un identifiant court basé sur le nom du fichier pour les fichiers temporaires.
    if repl.slot is None:
        auto_id = hashlib.sha256(repl.source.name.encode("utf-8")).hexdigest()[:8]
        safe_base = f"auto_{auto_id}"
    else:
        safe_base = f"slot_{repl.slot:02d}"

    ogg_path = temp_dir / f"{safe_base}.ogg"
    fsb_path = temp_dir / f"{safe_base}.fsb"


    
    # Tout passe en Vorbis stéréo 48 kHz avant insertion.
    audio_filter: Optional[str] = None

    if normalization_mode == "lufs":
        measured = measure_loudnorm(repl.source, ffmpeg, target_lufs, true_peak)
        if measured is None:
            print("  level: LUFS unknown, unchanged")
        else:
            print(
                f"  level: {measured['input_i']:+.2f} LUFS -> {target_lufs:+.2f} LUFS "
                f"(TP {true_peak:+.2f} dBTP)"
            )
            audio_filter = (
                "aformat=sample_rates=48000:channel_layouts=stereo,"
                f"loudnorm=I={target_lufs:.3f}:TP={true_peak:.3f}:LRA=11:"
                f"measured_I={measured['input_i']:.6f}:"
                f"measured_TP={measured['input_tp']:.6f}:"
                f"measured_LRA={measured['input_lra']:.6f}:"
                f"measured_thresh={measured['input_thresh']:.6f}:"
                f"offset={measured['target_offset']:.6f}:linear=true:print_format=summary"
            )
    elif normalization_mode == "peak":
        peak_db = measure_peak_db(repl.source, ffmpeg)
        boost_db = (
            max(0.0, target_peak_dbfs - peak_db)
            if peak_db is not None
            else 0.0
        )
        if peak_db is None:
            print("  level: peak unknown, unchanged")
        elif boost_db > 0.0001:
            print(
                f"  level: {peak_db:+.2f} dBFS -> {target_peak_dbfs:+.2f} dBFS "
                f"(+{boost_db:.2f} dB)"
            )
            audio_filter = f"volume={boost_db:.6f}dB"
        else:
            print(f"  level: {peak_db:+.2f} dBFS -> unchanged")
    else:
        print("  level: normalization disabled")

    ffmpeg_args = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(repl.source),
        "-map_metadata",
        "-1",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "48000",
    ]
    if audio_filter:
        ffmpeg_args += ["-af", audio_filter]
    ffmpeg_args += [
        "-c:a",
        "libvorbis",
        "-q:a",
        str(quality),
        str(ogg_path),
    ]
    run_command(ffmpeg_args)

    run_command([str(ogg_tool), str(ogg_path), str(fsb_path)])
    if not fsb_path.exists() or fsb_path.stat().st_size < 64:
        raise PatcherError(f"oggvorbis2fsb5 did not produce a valid donor for {repl.source.name}")

    donor = parse_fsb5(fsb_path.read_bytes())
    if len(donor.samples) != 1:
        raise PatcherError(f"Donor FSB for {repl.source.name} contains {len(donor.samples)} samples, expected 1")
    sample = donor.samples[0]
    if sample.frequency != 48000:
        raise PatcherError(
            f"Donor {repl.source.name} is {sample.frequency} Hz after normalization, expected 48000"
        )
    if sample.channels != 2:
        raise PatcherError(
            f"Donor {repl.source.name} has {sample.channels} channels after normalization, expected 2"
        )
    if sample.sample_count <= 0 or not sample.data:
        raise PatcherError(f"Donor {repl.source.name} has invalid duration/audio data")
    return sample


def merge_replacement_metadata(original: FsbSample, donor: FsbSample) -> list[MetadataChunk]:
    """Garde les métadonnées FSB du jeu et remplace celles liées au Vorbis."""
    donor_vorbis = [c for c in donor.metadata if c.chunk_type == 11]
    if len(donor_vorbis) != 1:
        raise PatcherError(
            f"Replacement donor for {donor.name!r} has {len(donor_vorbis)} VORBISDATA chunks; expected 1"
        )

    merged: list[MetadataChunk] = []
    replaced = False
    for chunk in original.metadata:
        if chunk.chunk_type == 11:
            if replaced:
                raise PatcherError(f"Original sample {original.name!r} has multiple VORBISDATA chunks")
            merged.append(MetadataChunk(11, donor_vorbis[0].data))
            replaced = True
        else:
            merged.append(MetadataChunk(chunk.chunk_type, chunk.data))

    if not replaced:
        raise PatcherError(f"Original sample {original.name!r} has no VORBISDATA metadata")
    return merged


@dataclass
class UnityObjectInfo:
    path_id: int
    byte_start: int
    byte_size: int
    type_id: int
    byte_start_field_offset: int
    byte_size_field_offset: int


@dataclass
class UnitySerializedFileInfo:
    metadata_size: int
    data_offset: int
    metadata_end: int
    object_count_field_offset: int
    object_table_end: int
    objects: list[UnityObjectInfo]


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def parse_unity_serialized_file(raw: bytes | bytearray) -> UnitySerializedFileInfo:
    """Lit le minimum nécessaire du SerializedFile Unity."""
    if len(raw) < 64:
        raise PatcherError("sharedassets0.assets is too small to be a Unity SerializedFile")

    version = struct.unpack_from(">I", raw, 8)[0]
    if version != 22:
        raise PatcherError(f"Unsupported Unity SerializedFile version {version}; Heatwarped expects 22")

    endian_flag = raw[16]
    if endian_flag != 0:
        raise PatcherError("Unsupported big-endian Unity SerializedFile")

    metadata_size = struct.unpack_from(">I", raw, 20)[0]
    file_size = struct.unpack_from(">Q", raw, 24)[0]
    data_offset = struct.unpack_from(">Q", raw, 32)[0]
    if file_size != len(raw):
        raise PatcherError(
            f"Unity SerializedFile size mismatch: header={file_size}, actual={len(raw)}"
        )
    if not (48 <= data_offset <= len(raw)):
        raise PatcherError("Unity SerializedFile has an invalid data offset")
    if metadata_size <= 0 or 48 + metadata_size > data_offset:
        raise PatcherError("Unity SerializedFile has inconsistent metadata size")

    pos = 48
    try:
        end = raw.index(0, pos)
    except ValueError as exc:
        raise PatcherError("Unity SerializedFile version string is unterminated") from exc
    unity_version = bytes(raw[pos:end]).decode("utf-8", errors="replace")
    pos = end + 1
    if not unity_version.startswith("6000."):
        raise PatcherError(
            f"Unexpected Unity version {unity_version!r}; refusing to rewrite an unknown sharedassets layout"
        )

    if pos + 9 > data_offset:
        raise PatcherError("Unity SerializedFile metadata is truncated")
    pos += 4  
    enable_type_tree = raw[pos]
    pos += 1
    if enable_type_tree:
        raise PatcherError("This patcher expects Heatwarped sharedassets0.assets without an embedded type tree")

    type_count = struct.unpack_from("<i", raw, pos)[0]
    pos += 4
    if not (0 < type_count < 4096):
        raise PatcherError(f"Invalid Unity type count {type_count}")


    for _ in range(type_count):
        if pos + 23 > data_offset:
            raise PatcherError("Unity SerializedType table is truncated")
        class_id = struct.unpack_from("<i", raw, pos)[0]
        pos += 4
        pos += 1  
        pos += 2  
        if class_id == 114:  
            if pos + 16 > data_offset:
                raise PatcherError("Unity MonoBehaviour type table is truncated")
            pos += 16
        pos += 16  

    if pos + 4 > data_offset:
        raise PatcherError("Unity object table is missing")
    object_count_field_offset = pos
    object_count = struct.unpack_from("<i", raw, pos)[0]
    pos += 4
    if not (0 < object_count < 10_000_000):
        raise PatcherError(f"Invalid Unity object count {object_count}")

    objects: list[UnityObjectInfo] = []
    for _ in range(object_count):
        pos = _align(pos, 4)
        if pos + 24 > data_offset:
            raise PatcherError("Unity object table is truncated")
        path_id = struct.unpack_from("<q", raw, pos)[0]
        pos += 8
        start_field = pos
        byte_start = struct.unpack_from("<q", raw, pos)[0]
        pos += 8
        size_field = pos
        byte_size = struct.unpack_from("<I", raw, pos)[0]
        pos += 4
        type_id = struct.unpack_from("<i", raw, pos)[0]
        pos += 4
        if byte_start < 0 or byte_size < 0 or data_offset + byte_start + byte_size > len(raw):
            raise PatcherError(f"Unity object PathID {path_id} points outside the file")
        objects.append(
            UnityObjectInfo(
                path_id=path_id,
                byte_start=byte_start,
                byte_size=byte_size,
                type_id=type_id,
                byte_start_field_offset=start_field,
                byte_size_field_offset=size_field,
            )
        )

    if len({o.path_id for o in objects}) != len(objects):
        raise PatcherError("Unity object table contains duplicate PathIDs")
    metadata_end = 48 + metadata_size
    if pos > metadata_end:
        raise PatcherError("Unity object table extends beyond metadata")
    return UnitySerializedFileInfo(
        metadata_size=metadata_size,
        data_offset=data_offset,
        metadata_end=metadata_end,
        object_count_field_offset=object_count_field_offset,
        object_table_end=pos,
        objects=objects,
    )


def _read_unity_string(payload: bytes, pos: int) -> tuple[str, int, bytes]:
    if pos + 4 > len(payload):
        raise PatcherError("Truncated Unity string")
    length = struct.unpack_from("<I", payload, pos)[0]
    if length > len(payload) - pos - 4:
        raise PatcherError("Invalid Unity string length")
    start = pos
    pos += 4
    end = pos + length
    text = payload[pos:end].decode("utf-8", errors="strict")
    pos = _align(end, 4)
    if pos > len(payload):
        raise PatcherError("Truncated Unity string padding")
    return text, pos, payload[start:pos]


def _pack_unity_string(text: str) -> bytes:
    encoded = text.encode("utf-8")
    if len(encoded) > 0xFFFFFFFF:
        raise PatcherError("Unity UI string is too long")
    out = bytearray(struct.pack("<I", len(encoded)) + encoded)
    out.extend(b"\0" * ((-len(out)) % 4))
    return bytes(out)


def _parse_music_track_payload(payload: bytes) -> Optional[tuple[str, str, str, bytes, bytes, bytes]]:
    """Lit un MusicTrack de Heatwarped."""
    if len(payload) < 28 + 12 + 20:
        return None
    try:
        p = 28
        internal, p, internal_raw = _read_unity_string(payload, p)
        title, p, _ = _read_unity_string(payload, p)
        artist, p, _ = _read_unity_string(payload, p)
    except (PatcherError, UnicodeDecodeError):
        return None
    trailer = payload[p:]
    if len(trailer) != 20:
        return None
    return internal, title, artist, payload[:28], internal_raw, trailer


def patch_sharedassets_music_ui(
    source_path: Path,
    replacements: dict[int, TrackReplacement],
    custom_graphs: Optional[dict[int, CustomEventGraph]] = None,
) -> tuple[bytes, list[dict]]:
    """Met à jour les MusicTrack et ajoute les pistes custom au Jukebox."""
    custom_graphs = custom_graphs or {}
    raw = source_path.read_bytes()
    info = parse_unity_serialized_file(raw)
    key_to_slot = {str(x["ui_key"]): int(x["slot"]) for x in PATCHABLE_MUSIC_SLOTS}

    found: dict[int, tuple[UnityObjectInfo, bytes, tuple[str, str, str, bytes, bytes, bytes]]] = {}
    for obj in info.objects:
        start = info.data_offset + obj.byte_start
        payload = raw[start : start + obj.byte_size]
        parsed = _parse_music_track_payload(payload)
        if parsed is None:
            continue
        internal = parsed[0]
        slot = key_to_slot.get(internal)
        if slot is None:
            continue
        if slot in found:
            raise PatcherError(f"Unity UI metadata contains duplicate MusicTrack key {internal!r}")
        found[slot] = (obj, payload, parsed)

    missing_all = [x["ui_key"] for x in PATCHABLE_MUSIC_SLOTS if x["slot"] not in found]
    if missing_all:
        raise PatcherError(
            "sharedassets0.assets does not match the analysed Heatwarped revision; missing MusicTrack objects: "
            + ", ".join(map(str, missing_all))
        )

    stock_replacements = {s: r for s, r in replacements.items() if s in SLOT_BY_NUMBER}
    custom_replacements = {s: r for s, r in replacements.items() if s >= 10}
    if set(custom_replacements) != set(custom_graphs):
        raise PatcherError("Internal custom-track FMOD/Unity graph mismatch")

    patched_payloads: dict[int, bytes] = {}
    ui_report: list[dict] = []


    for slot, repl in sorted(stock_replacements.items()):
        obj, old_payload, parsed = found[slot]
        internal, old_title, old_artist, prefix, internal_raw, trailer = parsed
        new_payload = (
            prefix
            + internal_raw
            + _pack_unity_string(repl.title)
            + _pack_unity_string(repl.artist)
            + trailer
        )
        verify = _parse_music_track_payload(new_payload)
        if verify is None or verify[0] != internal or verify[1] != repl.title or verify[2] != repl.artist:
            raise PatcherError(f"Internal Unity MusicTrack rebuild verification failed for slot {slot:02d}")
        if verify[5] != trailer:
            raise PatcherError(f"Unity MusicTrack GUID/trailer changed unexpectedly for slot {slot:02d}")
        patched_payloads[obj.path_id] = new_payload
        ui_report.append(
            {
                "slot": slot,
                "input_number": repl.slot,
                "kind": "replacement",
                "unity_internal_key": internal,
                "path_id": obj.path_id,
                "old_title": old_title,
                "old_artist": old_artist,
                "new_title": repl.title,
                "new_artist": repl.artist,
                "event_guid": trailer[-16:].hex(),
            }
        )


    
    custom_objects: list[tuple[int, int, bytes, int]] = []  
    custom_path_ids: dict[int, int] = {}
    if custom_replacements:
        carbon_obj, _, carbon_parsed = found[1]
        _, _, _, carbon_prefix, _, carbon_trailer = carbon_parsed
        if len(carbon_trailer) != 20:
            raise PatcherError("Unexpected Carbon MusicTrack trailer")
        next_pid = max(o.path_id for o in info.objects) + 1

        for slot, repl in sorted(custom_replacements.items()):
            graph = custom_graphs[slot]
            pid = next_pid
            next_pid += 1
            internal = f"Custom{slot:02d}"
            trailer = carbon_trailer[:4] + graph.event_guid
            payload = (
                carbon_prefix
                + _pack_unity_string(internal)
                + _pack_unity_string(repl.title)
                + _pack_unity_string(repl.artist)
                + trailer
            )
            parsed = _parse_music_track_payload(payload)
            if (
                parsed is None
                or parsed[0] != internal
                or parsed[1] != repl.title
                or parsed[2] != repl.artist
                or parsed[5][-16:] != graph.event_guid
            ):
                raise PatcherError(f"Could not build custom Unity MusicTrack for slot {slot:02d}")
            custom_objects.append((slot, pid, payload, carbon_obj.type_id))
            custom_path_ids[slot] = pid
            ui_report.append(
                {
                    "slot": slot,
                    "input_number": repl.slot,
                    "kind": "added",
                    "unity_internal_key": internal,
                    "path_id": pid,
                    "old_title": None,
                    "old_artist": None,
                    "new_title": repl.title,
                    "new_artist": repl.artist,
                    "event_guid": graph.event_guid.hex(),
                }
            )


        
        jukebox_obj = next((o for o in info.objects if o.path_id == JUKEBOX_PATH_ID), None)
        if jukebox_obj is None:
            raise PatcherError(f"Could not find analysed Jukebox PathID {JUKEBOX_PATH_ID}")
        js = info.data_offset + jukebox_obj.byte_start
        jukebox_payload = raw[js : js + jukebox_obj.byte_size]
        if len(jukebox_payload) < 36:
            raise PatcherError("Jukebox payload is unexpectedly small")
        try:
            name, jp, _ = _read_unity_string(jukebox_payload, 28)
        except Exception as exc:
            raise PatcherError("Could not parse Jukebox MonoBehaviour") from exc
        if name != "Jukebox" or jp + 4 > len(jukebox_payload):
            raise PatcherError("PathID 431 is not the expected Jukebox object")
        count_off = jp
        old_count = u32(jukebox_payload, count_off)
        ptr_start = count_off + 4
        ptr_end = ptr_start + old_count * 12
        if ptr_end > len(jukebox_payload):
            raise PatcherError("Truncated Jukebox MusicTrack array")

        existing_jukebox_pids: list[int] = []
        q = ptr_start
        for _ in range(old_count):
            file_id = struct.unpack_from("<i", jukebox_payload, q)[0]
            path_id = struct.unpack_from("<q", jukebox_payload, q + 4)[0]
            q += 12
            if file_id != 0:
                raise PatcherError("Jukebox contains an external MusicTrack reference; unsupported safely")
            existing_jukebox_pids.append(path_id)
        stock_music_pids = {found[s][0].path_id for s in found}
        if set(existing_jukebox_pids) != stock_music_pids:
            raise PatcherError(
                "Jukebox MusicTrack array no longer matches the analysed 8 stock sharedassets tracks"
            )

        extra_ptrs = b"".join(struct.pack("<iq", 0, custom_path_ids[s]) for s in sorted(custom_path_ids))
        new_jukebox = (
            jukebox_payload[:count_off]
            + p32(old_count + len(custom_path_ids))
            + jukebox_payload[ptr_start:ptr_end]
            + extra_ptrs
            + jukebox_payload[ptr_end:]
        )
        patched_payloads[jukebox_obj.path_id] = new_jukebox


    
    entry_blob = bytearray()
    new_entry_fields: dict[int, tuple[int, int]] = {}
    for slot, pid, payload, type_id in custom_objects:
        entry_off = info.object_table_end + len(entry_blob)
        entry_blob.extend(struct.pack("<qqIi", pid, 0, len(payload), type_id))
        new_entry_fields[pid] = (entry_off + 8, entry_off + 16)

    new_metadata_size = info.metadata_size + len(entry_blob)
    metadata = bytearray(
        raw[: info.object_table_end]
        + entry_blob
        + raw[info.object_table_end : info.metadata_end]
    )
    if len(metadata) != 48 + new_metadata_size:
        raise PatcherError("Internal Unity metadata-size rebuild mismatch")
    struct.pack_into(">I", metadata, 20, new_metadata_size)
    struct.pack_into(
        "<i",
        metadata,
        info.object_count_field_offset,
        len(info.objects) + len(custom_objects),
    )


    new_data_offset = _align(48 + new_metadata_size, 16)
    if new_data_offset < len(metadata):
        raise PatcherError("Internal Unity data-offset rebuild underflow")
    metadata.extend(b"\0" * (new_data_offset - len(metadata)))
    struct.pack_into(">Q", metadata, 32, new_data_offset)

    new_data = bytearray()

    for obj in sorted(info.objects, key=lambda x: x.byte_start):
        new_start = _align(len(new_data), 16)
        if new_start > len(new_data):
            new_data.extend(b"\0" * (new_start - len(new_data)))
        old_abs = info.data_offset + obj.byte_start
        payload = patched_payloads.get(obj.path_id, raw[old_abs : old_abs + obj.byte_size])
        struct.pack_into("<q", metadata, obj.byte_start_field_offset, new_start)
        struct.pack_into("<I", metadata, obj.byte_size_field_offset, len(payload))
        new_data.extend(payload)


    for slot, pid, payload, type_id in custom_objects:
        new_start = _align(len(new_data), 16)
        if new_start > len(new_data):
            new_data.extend(b"\0" * (new_start - len(new_data)))
        start_field, size_field = new_entry_fields[pid]
        struct.pack_into("<q", metadata, start_field, new_start)
        struct.pack_into("<I", metadata, size_field, len(payload))
        new_data.extend(payload)

    rebuilt = bytearray(metadata + new_data)
    struct.pack_into(">Q", rebuilt, 24, len(rebuilt))


    check = parse_unity_serialized_file(rebuilt)
    old_by_pid = {o.path_id: o for o in info.objects}
    new_by_pid = {o.path_id: o for o in check.objects}
    expected_pids = set(old_by_pid) | set(custom_path_ids.values())
    if set(new_by_pid) != expected_pids:
        raise PatcherError("Unity object table PathIDs are wrong after adding custom tracks")

    allowed_changed = set(patched_payloads)
    for pid, old_obj in old_by_pid.items():
        new_obj = new_by_pid[pid]
        old_bytes = raw[
            info.data_offset + old_obj.byte_start : info.data_offset + old_obj.byte_start + old_obj.byte_size
        ]
        new_bytes = rebuilt[
            check.data_offset + new_obj.byte_start : check.data_offset + new_obj.byte_start + new_obj.byte_size
        ]
        if pid not in allowed_changed and new_bytes != old_bytes:
            raise PatcherError(f"Unity safety check failed: untouched object PathID {pid} changed")


    for slot, pid, payload, _ in custom_objects:
        obj = new_by_pid[pid]
        final_payload = bytes(
            rebuilt[
                check.data_offset + obj.byte_start :
                check.data_offset + obj.byte_start + obj.byte_size
            ]
        )
        parsed = _parse_music_track_payload(final_payload)
        graph = custom_graphs[slot]
        repl = custom_replacements[slot]
        if (
            parsed is None
            or parsed[1] != repl.title
            or parsed[2] != repl.artist
            or parsed[5][-16:] != graph.event_guid
        ):
            raise PatcherError(f"Final Unity validation failed for custom slot {slot:02d}")

    if custom_objects:
        jo = new_by_pid[JUKEBOX_PATH_ID]
        jpayload = bytes(
            rebuilt[
                check.data_offset + jo.byte_start :
                check.data_offset + jo.byte_start + jo.byte_size
            ]
        )
        name, jp, _ = _read_unity_string(jpayload, 28)
        count = u32(jpayload, jp)
        q = jp + 4
        pids = []
        for _ in range(count):
            file_id = struct.unpack_from("<i", jpayload, q)[0]
            pid = struct.unpack_from("<q", jpayload, q + 4)[0]
            q += 12
            if file_id != 0:
                raise PatcherError("Final Jukebox validation found an external PPtr")
            pids.append(pid)
        for slot, pid in custom_path_ids.items():
            if pid not in pids:
                raise PatcherError(f"Custom slot {slot:02d} is missing from final Jukebox array")

    return bytes(rebuilt), ui_report


def _parse_music_playlist_payload(payload: bytes) -> Optional[tuple[str, int, list[tuple[int, int]], bytes]]:
    """Lit une MusicPlaylist de Heatwarped."""
    if len(payload) < 36:
        return None
    try:
        name, pos, _ = _read_unity_string(payload, 28)
    except (PatcherError, UnicodeDecodeError):
        return None
    if pos + 4 > len(payload):
        return None
    count_off = pos
    count = u32(payload, count_off)
    ptr_start = count_off + 4
    ptr_end = ptr_start + count * 12
    if ptr_end > len(payload):
        return None
    refs: list[tuple[int, int]] = []
    q = ptr_start
    for _ in range(count):
        file_id = struct.unpack_from("<i", payload, q)[0]
        path_id = struct.unpack_from("<q", payload, q + 4)[0]
        refs.append((file_id, path_id))
        q += 12
    return name, count_off, refs, payload[ptr_end:]


def extract_final_jukebox_path_ids(sharedassets_raw: bytes) -> list[int]:
    """Retourne l'ordre final des MusicTrack du Jukebox."""
    info = parse_unity_serialized_file(sharedassets_raw)
    obj = next((o for o in info.objects if o.path_id == JUKEBOX_PATH_ID), None)
    if obj is None:
        raise PatcherError(f"Could not find Jukebox PathID {JUKEBOX_PATH_ID} in final sharedassets0.assets")
    payload = sharedassets_raw[
        info.data_offset + obj.byte_start : info.data_offset + obj.byte_start + obj.byte_size
    ]
    parsed = _parse_music_playlist_payload(payload)
    if parsed is None or parsed[0] != "Jukebox":
        raise PatcherError("Final sharedassets0 Jukebox payload is not the analysed MusicPlaylist layout")
    refs = parsed[2]
    if not refs:
        raise PatcherError("Final Jukebox is empty")
    if any(file_id != 0 for file_id, _ in refs):
        raise PatcherError("Final Jukebox unexpectedly contains external MusicTrack references")
    return [path_id for _, path_id in refs]


def patch_resources_playlists(
    source_path: Path,
    jukebox_path_ids: list[int],
    mode: str = "full",
) -> tuple[bytes, dict]:
    """Applique stock/full à toutes les MusicPlaylist de resources.assets."""
    mode = mode.lower().strip()
    if mode not in {"stock", "full"}:
        raise PatcherError(f"Unsupported playlist mode: {mode}")

    raw = source_path.read_bytes()
    info = parse_unity_serialized_file(raw)

    MUSIC_PLAYLIST_SCRIPT_PATH_ID = 693
    SHAREDASSETS_FILE_ID = 3
    EXPECTED_STOCK_PLAYLISTS = {
        "Drift": [(3, 437)],
        "FreeRoam": [(3, 436), (3, 435), (3, 434)],
        "MainMenu": [(3, 433)],
        "Racing": [(3, 438), (3, 432), (3, 439)],
    }

    candidates: dict[str, tuple[object, tuple[str, int, list[tuple[int, int]], bytes], bytes]] = {}
    for obj in info.objects:
        start = info.data_offset + obj.byte_start
        payload = raw[start : start + obj.byte_size]
        if len(payload) < 28:
            continue
        script_file_id = struct.unpack_from("<i", payload, 16)[0]
        script_path_id = struct.unpack_from("<q", payload, 20)[0]
        if script_file_id != 1 or script_path_id != MUSIC_PLAYLIST_SCRIPT_PATH_ID:
            continue

        parsed = _parse_music_playlist_payload(payload)
        if parsed is None:
            continue

        name = parsed[0]
        if name in candidates:
            raise PatcherError(f"resources.assets contains multiple {name} MusicPlaylist objects")
        candidates[name] = (obj, parsed, payload)

    if set(candidates) != set(EXPECTED_STOCK_PLAYLISTS):
        raise PatcherError(
            "resources.assets MusicPlaylist set no longer matches the analysed stock build: "
            f"expected {sorted(EXPECTED_STOCK_PLAYLISTS)}, got {sorted(candidates)}"
        )

    if len(jukebox_path_ids) < 8:
        raise PatcherError("Final Jukebox has fewer than the 8 stock MusicTracks")
    if len(set(jukebox_path_ids)) != len(jukebox_path_ids):
        raise PatcherError("Final Jukebox contains duplicate MusicTrack PathIDs")

    full_refs = [(SHAREDASSETS_FILE_ID, pid) for pid in jukebox_path_ids]
    custom_count = max(0, len(jukebox_path_ids) - 8)

    playlist_reports: list[dict] = []
    for name in ("Drift", "FreeRoam", "MainMenu", "Racing"):
        obj, parsed, _ = candidates[name]
        old_name, _, old_refs, _ = parsed
        expected_refs = EXPECTED_STOCK_PLAYLISTS[name]
        if old_refs != expected_refs:
            raise PatcherError(
                f"resources.assets {name} playlist no longer matches the analysed stock build: "
                f"expected {expected_refs}, got {old_refs}"
            )

        new_refs = list(old_refs) if mode == "stock" else list(full_refs)
        playlist_reports.append(
            {
                "playlist": old_name,
                "path_id": obj.path_id,
                "old_refs": old_refs,
                "new_refs": new_refs,
                "old_count": len(old_refs),
                "new_count": len(new_refs),
            }
        )

    if mode == "stock":
        return raw, {
            "mode": mode,
            "changed": False,
            "custom_count": custom_count,
            "policy": "all stock playlists unchanged",
            "playlists": playlist_reports,
        }

    replacements_by_pid: dict[int, bytes] = {}
    for name in ("Drift", "FreeRoam", "MainMenu", "Racing"):
        obj, parsed, old_payload = candidates[name]
        _, count_off, _, tail = parsed
        new_payload = (
            old_payload[:count_off]
            + p32(len(full_refs))
            + b"".join(struct.pack("<iq", file_id, path_id) for file_id, path_id in full_refs)
            + tail
        )
        replacements_by_pid[obj.path_id] = new_payload

    metadata = bytearray(raw[: info.data_offset])
    new_data = bytearray()
    old_by_pid = {o.path_id: o for o in info.objects}
    changed_pids = set(replacements_by_pid)

    for obj in sorted(info.objects, key=lambda x: x.byte_start):
        new_start = _align(len(new_data), 16)
        if new_start > len(new_data):
            new_data.extend(b"\0" * (new_start - len(new_data)))

        old_abs = info.data_offset + obj.byte_start
        payload = replacements_by_pid.get(
            obj.path_id,
            raw[old_abs : old_abs + obj.byte_size],
        )
        struct.pack_into("<q", metadata, obj.byte_start_field_offset, new_start)
        struct.pack_into("<I", metadata, obj.byte_size_field_offset, len(payload))
        new_data.extend(payload)

    rebuilt = bytearray(metadata + new_data)
    struct.pack_into(">Q", rebuilt, 24, len(rebuilt))

    check = parse_unity_serialized_file(rebuilt)
    new_by_pid = {o.path_id: o for o in check.objects}
    if set(new_by_pid) != set(old_by_pid):
        raise PatcherError("resources.assets object table changed unexpectedly")

    for pid, before_obj in old_by_pid.items():
        after_obj = new_by_pid[pid]
        before = raw[
            info.data_offset + before_obj.byte_start :
            info.data_offset + before_obj.byte_start + before_obj.byte_size
        ]
        after = rebuilt[
            check.data_offset + after_obj.byte_start :
            check.data_offset + after_obj.byte_start + after_obj.byte_size
        ]
        if pid not in changed_pids and after != before:
            raise PatcherError(f"resources.assets safety check failed: untouched object PathID {pid} changed")

    for name in ("Drift", "FreeRoam", "MainMenu", "Racing"):
        old_obj, _, _ = candidates[name]
        final_obj = new_by_pid[old_obj.path_id]
        final_payload = bytes(rebuilt[
            check.data_offset + final_obj.byte_start :
            check.data_offset + final_obj.byte_start + final_obj.byte_size
        ])
        final_parsed = _parse_music_playlist_payload(final_payload)
        if final_parsed is None or final_parsed[0] != name or final_parsed[2] != full_refs:
            raise PatcherError(f"Final {name} playlist validation failed")

    # WARPED est un MusicTrack séparé dans resources.assets et ne doit jamais bouger.
    warped_obj = old_by_pid.get(5535)
    if warped_obj is not None:
        new_warped = new_by_pid[5535]
        before_warped = raw[
            info.data_offset + warped_obj.byte_start :
            info.data_offset + warped_obj.byte_start + warped_obj.byte_size
        ]
        after_warped = rebuilt[
            check.data_offset + new_warped.byte_start :
            check.data_offset + new_warped.byte_start + new_warped.byte_size
        ]
        if after_warped != before_warped:
            raise PatcherError("resources.assets safety check failed: WARPED MusicTrack changed")

    return bytes(rebuilt), {
        "mode": mode,
        "changed": bytes(rebuilt) != raw,
        "custom_count": custom_count,
        "policy": "all playlists mirror final Jukebox; WARPED excluded",
        "playlists": playlist_reports,
    }


def choose_end_marker(
    old_trigger: int,
    old_end: int,
    new_length: int,
    policy: str,
) -> int:
    if policy == "full":
        return new_length
    if policy == "preserve_original":
        return old_end

    tail = max(0, old_trigger - old_end)
    return max(0, new_length - tail)


def patch_timeline(
    bank: bytearray,
    timeline: TimelineInfo,
    original_sample_count: int,
    new_sample_count: int,
    policy: str,
    padding_samples: int,
) -> dict:

    

    

    

    

    # Garde au moins 1 sample après l'audio pour laisser FMOD traiter le marqueur End.
    native_postroll = 0
    if timeline.old_trigger_lengths:
        native_postroll = max(0, max(timeline.old_trigger_lengths) - original_sample_count)
    safe_postroll = max(1, native_postroll)
    new_trigger = new_sample_count + safe_postroll + max(0, padding_samples)
    if new_trigger > 0xFFFFFFFF:
        raise PatcherError("Replacement timeline length exceeds FMOD uint32 range")

    for off in timeline.trigger_length_offsets:
        bank[off : off + 4] = p32(new_trigger)

    new_end_values: list[int] = []
    for i, off in enumerate(timeline.end_marker_position_offsets):
        old_trigger = timeline.old_trigger_lengths[min(i, len(timeline.old_trigger_lengths) - 1)] if timeline.old_trigger_lengths else original_sample_count
        old_end = timeline.old_end_positions[i]
        if policy == "full":

            

            new_end = new_trigger
        else:
            new_end = choose_end_marker(old_trigger, old_end, new_trigger, policy)
        bank[off : off + 4] = p32(new_end)
        new_end_values.append(new_end)

    return {
        "old_trigger_lengths": timeline.old_trigger_lengths,
        "native_postroll_samples": native_postroll,
        "safe_postroll_samples": safe_postroll,
        "new_trigger_length": new_trigger,
        "old_end_markers": timeline.old_end_positions,
        "new_end_markers": new_end_values,
    }


def replace_embedded_fsb(bank: bytearray, fsb_off: int, snd_off: int, old_fsb_size: int, new_fsb: bytes) -> bytearray:
    old_end = fsb_off + old_fsb_size
    if old_end > len(bank):
        raise PatcherError("Old FSB boundary exceeds bank")
    if len(new_fsb) > 0xFFFFFFFF:
        raise PatcherError("Rebuilt FSB exceeds FMOD's 32-bit SoundData length field")


    

    
    # SNDH contient aussi la taille du FSB : il faut la mettre à jour avec SND/RIFF.
    _, sndh_length_off, declared_old_fsb_size = find_sound_data_header_entry(
        bank, fsb_off, search_end=snd_off
    )
    if declared_old_fsb_size != old_fsb_size:
        raise PatcherError(
            "SNDH/FSB size mismatch in input bank: "
            f"SNDH={declared_old_fsb_size}, embedded FSB={old_fsb_size}"
        )

    new_bank = bytearray(bank[:fsb_off] + new_fsb + bank[old_end:])


    
    new_bank[sndh_length_off : sndh_length_off + 4] = p32(len(new_fsb))


    prefix_size = fsb_off - (snd_off + 8)
    if prefix_size < 0:
        raise PatcherError("Invalid SND/FSB layout")
    new_snd_size = prefix_size + len(new_fsb)
    if new_snd_size > 0xFFFFFFFF:
        raise PatcherError("Rebuilt SND chunk exceeds RIFF's 32-bit chunk-size field")
    new_bank[snd_off + 4 : snd_off + 8] = p32(new_snd_size)


    new_riff_size = len(new_bank) - 8
    if new_riff_size > 0xFFFFFFFF:
        raise PatcherError("Rebuilt Master.bank exceeds RIFF's 32-bit size field")
    new_bank[4:8] = p32(new_riff_size)
    return new_bank


def _fmod_list_count(bank: bytes | bytearray, list_type: bytes, search_end: int) -> int:
    _, _, children_start, _ = find_list_node(bank, list_type, search_end)
    if bytes(bank[children_start : children_start + 4]) != b"LCNT" or u32(bank, children_start + 4) != 4:
        raise PatcherError(f"LIST/{list_type.decode()} has an unexpected LCNT layout")
    return u32(bank, children_start + 8)


def check_patch_files(
    master_bank: Path,
    sharedassets_path: Path,
    resources_path: Path,
    tracks_dir: Path,
) -> list[TrackReplacement]:
    if not master_bank.exists():
        raise PatcherError(
            f"Missing input file: {master_bank}\n"
            "Copy Heatwarped_Data\\StreamingAssets\\Master.bank into the input folder."
        )
    if not sharedassets_path.exists():
        raise PatcherError(
            f"Missing input file: {sharedassets_path}\n"
            "Copy Heatwarped_Data\\sharedassets0.assets into the input folder."
        )
    if not resources_path.exists():
        raise PatcherError(
            f"Missing input file: {resources_path}\n"
            "Copy Heatwarped_Data\\resources.assets into the input folder."
        )

    input_replacements = scan_tracks(tracks_dir)
    if not input_replacements:
        raise PatcherError(
            f"No tracks found in {tracks_dir}\n"
            "Use 01-08 to replace stock music, 09-99 to order custom tracks, "
            "or just drop supported audio files in the folder to append them at the end."
        )
    return input_replacements


def patch(
    master_bank: Path,
    sharedassets_path: Path,
    resources_path: Path,
    input_replacements: list[TrackReplacement],
    output_dir: Path,
    ffmpeg: Path,
    ogg_tool: Path,
    config: dict,
) -> None:
    fetch_metadata = bool(config.get("fetch_metadata", False))
    normalization_mode = str(config.get("normalization_mode", "lufs")).lower()
    target_lufs = float(config.get("target_lufs", -9.0))
    true_peak = float(config.get("true_peak", -1.0))
    target_peak_dbfs = float(config.get("target_peak_dbfs", 0.0))

    for repl in input_replacements:
        metadata = fetch_audio_metadata(repl.source, ffmpeg) if fetch_metadata else None
        merge_track_metadata(repl, metadata)

    replacements, stock_replacements, custom_replacements, custom_input_map = resolve_track_layout(
        input_replacements
    )

    raw = master_bank.read_bytes()
    original_sha = hashlib.sha256(raw).hexdigest()
    fsb_off, snd_off, old_fsb_size = find_embedded_fsb(raw)
    original_fsb_raw = raw[fsb_off : fsb_off + old_fsb_size]
    fsb = parse_fsb5(original_fsb_raw)
    validate_heatwarped_bank(fsb, allow_extra=False)
    original_samples = list(fsb.samples)

    no_op = serialize_fsb5(fsb)
    if no_op != original_fsb_raw:
        raise PatcherError(
            "Safety check failed: FSB parser/rebuilder cannot reproduce this bank byte-for-byte. "
            "No output was written."
        )

    timelines = discover_music_timelines(raw, fsb_off)
    protected_idx = PROTECTED_MUSIC_SLOT["sample_index"]
    protected_original_sample = fsb.samples[protected_idx]
    protected_original_timeline = timelines[protected_idx]

    quality = int(config.get("vorbis_quality", 6))
    quality = max(-1, min(10, quality))
    policy = str(config.get("end_marker_policy", "full")).lower()
    padding_ms = int(config.get("timeline_padding_ms", 0))
    padding_samples = max(0, round(padding_ms * 48))
    playlist_mode = str(config.get("playlist_mode", "full")).lower()

    patched_bank_metadata = bytearray(raw)
    manifest_tracks: list[dict] = []
    report_lines = [
        f"Heatwarped Music Patcher v{APP_VERSION}",
        f"Master.bank: {original_sha}",
        f"Playlists: {playlist_mode}",
        f"Normalization: {normalization_mode}",
        f"Stock replaced: {len(stock_replacements)}",
        f"Custom added: {len(custom_replacements)}",
        "",
    ]

    custom_audio: dict[int, tuple[int, FsbSample, FsbSample]] = {}

    with tempfile.TemporaryDirectory(prefix="heatwarped_music_") as td:
        temp_dir = Path(td)
        for slot in sorted(replacements):
            repl = replacements[slot]
            if slot in SLOT_BY_NUMBER:
                slot_info = SLOT_BY_NUMBER[slot]
                print(f"[{slot:02d}] {slot_info['sample_name']} <- {repl.artist} - {repl.title}")
            else:
                source_slot = f"{repl.slot:02d}" if repl.slot is not None else "AUTO"
                label = f"{repl.artist} - {repl.title}" if repl.artist else repl.title
                print(f"[{source_slot} -> {slot:02d}] custom <- {label}")

            donor = build_donor_fsb(
                repl,
                ffmpeg,
                ogg_tool,
                quality,
                temp_dir,
                normalization_mode,
                target_lufs,
                true_peak,
                target_peak_dbfs,
            )

            if slot in SLOT_BY_NUMBER:
                slot_info = SLOT_BY_NUMBER[slot]
                idx = slot_info["sample_index"]
                original_sample = fsb.samples[idx]
                fsb.samples[idx] = FsbSample(
                    name=original_sample.name,
                    frequency_code=donor.frequency_code,
                    channels_flag=donor.channels_flag,
                    sample_count=donor.sample_count,
                    metadata=merge_replacement_metadata(original_sample, donor),
                    data=donor.data,
                )

                tl_result = patch_timeline(
                    patched_bank_metadata,
                    timelines[idx],
                    original_sample.sample_count,
                    donor.sample_count,
                    policy,
                    padding_samples,
                )
                duration = donor.sample_count / 48000.0
                manifest_tracks.append(
                    {
                        "slot": slot,
                        "input_number": repl.slot,
                        "kind": "replacement",
                        "base_sample": slot_info["sample_name"],
                        "unity_internal_key": slot_info["ui_key"],
                        "fsb_sample_index": idx,
                        "event_path": slot_info["event_path"],
                        "event_guid": None,
                        "artist": repl.artist,
                        "title": repl.title,
                        "source_filename": repl.source.name,
                        "sample_count_48000hz": donor.sample_count,
                        "duration_seconds": duration,
                        "timeline": tl_result,
                    }
                )
                report_lines.extend(
                    [
                        f"{slot:02d} REPLACE {slot_info['sample_name']}",
                        f"  file: {repl.source.name}",
                        f"  FSB: #{idx}",
                        f"  length: {duration:.3f}s",
                        "",
                    ]
                )
            else:
                idx = len(fsb.samples)
                carbon_sample = original_samples[0]
                custom_sample = FsbSample(
                    name=f"custom_{slot:02d}",
                    frequency_code=donor.frequency_code,
                    channels_flag=donor.channels_flag,
                    sample_count=donor.sample_count,
                    metadata=merge_replacement_metadata(carbon_sample, donor),
                    data=donor.data,
                )
                fsb.samples.append(custom_sample)
                custom_audio[slot] = (idx, donor, carbon_sample)

    # Les slots stock non remplacés gardent leur gain d'origine.
    # Seuls les remplacements 01-08 passent à 0 dB.
    for slot in sorted(stock_replacements):
        idx = SLOT_BY_NUMBER[slot]["sample_index"]
        set_music_event_gain_db(patched_bank_metadata, timelines[idx].timeline_guid, 0.0)

    custom_graphs: dict[int, CustomEventGraph] = {}
    if custom_audio:
        custom_graphs = clone_custom_music_graphs(
            raw,
            patched_bank_metadata,
            [(slot, custom_audio[slot][0]) for slot in sorted(custom_audio)],
        )

        shifted_fsb_off = patched_bank_metadata.find(b"FSB5")
        if shifted_fsb_off < 0:
            raise PatcherError("FSB missing after adding custom Event graphs")
        for slot in sorted(custom_audio):
            idx, donor, carbon_sample = custom_audio[slot]
            repl = custom_replacements[slot]
            graph = custom_graphs[slot]
            tl = discover_timeline_for_sample(patched_bank_metadata, shifted_fsb_off, idx)
            if tl.resource_guid != graph.resource_guid or tl.timeline_guid != graph.timeline_guid:
                raise PatcherError(f"Custom FMOD graph resolution mismatch for slot {slot:02d}")
            tl_result = patch_timeline(
                patched_bank_metadata,
                tl,
                carbon_sample.sample_count,
                donor.sample_count,
                policy,
                padding_samples,
            )
            duration = donor.sample_count / 48000.0
            manifest_tracks.append(
                {
                    "slot": slot,
                    "input_number": repl.slot,
                    "kind": "added",
                    "base_sample": None,
                    "unity_internal_key": f"Custom{slot:02d}",
                    "fsb_sample_index": idx,
                    "event_path": None,
                    "event_guid": graph.event_guid.hex(),
                    "timeline_guid": graph.timeline_guid.hex(),
                    "resource_guid": graph.resource_guid.hex(),
                    "artist": repl.artist,
                    "title": repl.title,
                    "source_filename": repl.source.name,
                    "sample_count_48000hz": donor.sample_count,
                    "duration_seconds": duration,
                    "timeline": tl_result,
                }
            )
            report_lines.extend(
                [
                    f"{f'{repl.slot:02d}' if repl.slot is not None else 'AUTO'} -> {slot:02d} CUSTOM",
                    f"  file: {repl.source.name}",
                    f"  FSB: #{idx}",
                    f"  length: {duration:.3f}s",
                    "",
                ]
            )

    current_fsb_off, current_snd_off, current_old_fsb_size = find_embedded_fsb(patched_bank_metadata)
    if current_old_fsb_size != old_fsb_size:
        raise PatcherError("FMOD metadata insertion unexpectedly changed the old embedded FSB byte range")
    _, _, declared_old_fsb_size = find_sound_data_header_entry(
        patched_bank_metadata, current_fsb_off, search_end=current_snd_off
    )
    if declared_old_fsb_size != old_fsb_size:
        raise PatcherError("SNDH length changed unexpectedly before FSB serialization")

    new_fsb = serialize_fsb5(fsb)
    report_lines.extend(
        [
            "FSB",
            f"  samples: {STOCK_FSB_SAMPLE_COUNT} -> {len(fsb.samples)}",
            f"  size: {old_fsb_size} -> {len(new_fsb)} bytes ({len(new_fsb) - old_fsb_size:+d})",
            "",
        ]
    )
    final_bank = replace_embedded_fsb(
        patched_bank_metadata,
        current_fsb_off,
        current_snd_off,
        current_old_fsb_size,
        new_fsb,
    )

    final_fsb_off, final_snd_off, final_fsb_size = find_embedded_fsb(final_bank)
    final_sndh_off_field, _, final_sndh_length = find_sound_data_header_entry(
        final_bank, final_fsb_off, search_end=final_snd_off
    )
    if u32(final_bank, final_sndh_off_field) != final_fsb_off:
        raise PatcherError("Final validation failed: SNDH FSBOffset does not match actual FSB")
    if final_sndh_length != final_fsb_size:
        raise PatcherError(
            "Final validation failed: SNDH SoundData length does not match rebuilt FSB "
            f"({final_sndh_length} != {final_fsb_size})"
        )

    final_fsb = parse_fsb5(bytes(final_bank[final_fsb_off : final_fsb_off + final_fsb_size]))
    validate_heatwarped_bank(final_fsb, allow_extra=bool(custom_replacements))
    expected_final_count = STOCK_FSB_SAMPLE_COUNT + len(custom_replacements)
    if len(final_fsb.samples) != expected_final_count:
        raise PatcherError(
            f"Final FSB sample count mismatch: expected {expected_final_count}, got {len(final_fsb.samples)}"
        )

    final_stock_timelines = discover_music_timelines(final_bank, final_fsb_off)
    for track in manifest_tracks:
        idx = track["fsb_sample_index"]
        if idx < STOCK_FSB_SAMPLE_COUNT:
            final_tl = final_stock_timelines[idx]
        else:
            final_tl = discover_timeline_for_sample(final_bank, final_fsb_off, idx)
        native_postroll = int(track["timeline"].get("native_postroll_samples", 0))
        safe_postroll = max(1, native_postroll)
        expected = track["sample_count_48000hz"] + safe_postroll + padding_samples
        if not final_tl.old_trigger_lengths or any(v != expected for v in final_tl.old_trigger_lengths):
            raise PatcherError(
                f"Final validation failed for slot {track['slot']:02d}: Timeline length mismatch"
            )
        if policy == "full" and final_tl.old_end_positions:
            if any(v != expected for v in final_tl.old_end_positions):
                raise PatcherError(
                    f"Final validation failed for slot {track['slot']:02d}: End marker mismatch"
                )

    patched_stock_indices = {SLOT_BY_NUMBER[s]["sample_index"] for s in stock_replacements}
    for idx, before in enumerate(original_samples):
        after = final_fsb.samples[idx]
        if idx in patched_stock_indices:
            continue
        if after != before:
            raise PatcherError(
                f"Final validation failed: untouched stock FSB sample #{idx} ({before.name}) changed. "
                "This includes engine/UI/SFX/WARPED safety. No output was written."
            )

    final_protected_sample = final_fsb.samples[protected_idx]
    if final_protected_sample != protected_original_sample:
        raise PatcherError(
            "Final validation failed: protected slot 09 / WARPED changed. No output was written."
        )
    final_protected_timeline = final_stock_timelines[protected_idx]
    if (
        final_protected_timeline.resource_guid != protected_original_timeline.resource_guid
        or final_protected_timeline.timeline_guid != protected_original_timeline.timeline_guid
        or final_protected_timeline.old_trigger_lengths != protected_original_timeline.old_trigger_lengths
        or final_protected_timeline.old_end_positions != protected_original_timeline.old_end_positions
    ):
        raise PatcherError(
            "Final validation failed: protected slot 09 / WARPED Timeline changed. No output was written."
        )

    if custom_graphs:
        count_expectations = {
            b"IBSS": 99 + len(custom_graphs),
            b"GBSS": 100 + len(custom_graphs),
            b"MBSS": 100 + len(custom_graphs),
            b"EVTS": 99 + len(custom_graphs),
            b"TLNS": 99 + len(custom_graphs),
            b"WAIS": 111 + len(custom_graphs),
            b"WAVS": 110 + len(custom_graphs),
        }
        for list_type, expected_count in count_expectations.items():
            got = _fmod_list_count(final_bank, list_type, final_fsb_off)
            if got != expected_count:
                raise PatcherError(
                    f"Final LIST/{list_type.decode()} count mismatch: expected {expected_count}, got {got}"
                )
        resources = parse_waveform_resources(final_bank, final_fsb_off)
        for slot, graph in custom_graphs.items():
            if resources.get(graph.sample_index) != graph.resource_guid:
                raise PatcherError(f"Final custom WAV mapping failed for slot {slot:02d}")


        

        

        # Dernier check sur les GUID avant d'écrire les fichiers.
        _validate_custom_graph_topology(final_bank, custom_graphs)

    final_sharedassets, ui_report = patch_sharedassets_music_ui(
        sharedassets_path, replacements, custom_graphs
    )
    final_jukebox_path_ids = extract_final_jukebox_path_ids(final_sharedassets)
    final_resources, playlist_report = patch_resources_playlists(
        resources_path, final_jukebox_path_ids, playlist_mode
    )
    report_lines.extend(
        [
            "Playlists",
            f"  mode: {playlist_report['mode']}",
            f"  resources.assets changed: {playlist_report['changed']}",
        ]
    )
    for playlist in playlist_report["playlists"]:
        report_lines.append(
            f"  {playlist['playlist']}: {playlist['old_count']} -> {playlist['new_count']} tracks"
        )
    report_lines.append("")
    ui_by_slot = {x["slot"]: x for x in ui_report}
    for track in manifest_tracks:
        track["unity_ui"] = ui_by_slot.get(track["slot"])

    output_dir.mkdir(parents=True, exist_ok=True)
    game_data_dir = output_dir / "Heatwarped_Data"
    streaming_dir = game_data_dir / "StreamingAssets"
    streaming_dir.mkdir(parents=True, exist_ok=True)

    out_bank = streaming_dir / "Master.bank"
    out_bank.write_bytes(final_bank)
    out_sharedassets = game_data_dir / "sharedassets0.assets"
    out_sharedassets.write_bytes(final_sharedassets)
    out_resources = game_data_dir / "resources.assets"
    out_resources.write_bytes(final_resources)

    manifest = {
        "patcher": "Heatwarped Music Patcher",
        "version": APP_VERSION,
        "input_master_bank_sha256": original_sha,
        "output_master_bank_sha256": hashlib.sha256(final_bank).hexdigest(),
        "input_sharedassets0_sha256": sha256_file(sharedassets_path),
        "output_sharedassets0_sha256": hashlib.sha256(final_sharedassets).hexdigest(),
        "input_resources_sha256": sha256_file(resources_path),
        "output_resources_sha256": hashlib.sha256(final_resources).hexdigest(),
        "playlists": playlist_report,
        "slot_policy": {
            "01-08": "first occurrence replaces stock; duplicates become first custom tracks",
            "09-99": "custom order; duplicates kept and compacted to internal slots 10+",
            "unnumbered": "custom tracks appended after all numbered files",
            "WARPED": "protected",
        },
        "custom_input_to_internal_slot": custom_input_map,
        "display_metadata_note": "First 01-08 occurrence replaces stock; duplicate 01-08 entries become first customs; duplicate 09-99 entries are kept; unnumbered audio is appended last.",
        "normalization_mode": normalization_mode,
        "target_lufs": target_lufs,
        "true_peak": true_peak,
        "target_peak_dbfs": target_peak_dbfs,
        "fetch_metadata": fetch_metadata,
        "protected_track": {
            "slot": PROTECTED_MUSIC_SLOT["slot"],
            "base_sample": PROTECTED_MUSIC_SLOT["sample_name"],
            "fsb_sample_index": PROTECTED_MUSIC_SLOT["sample_index"],
            "event_path": PROTECTED_MUSIC_SLOT["event_path"],
            "policy": "protected",
        },
        "tracks": sorted(manifest_tracks, key=lambda x: x["slot"]),
    }
    manifest_path = output_dir / "track_manifest.json"
    report_path = output_dir / "patch_report.txt"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    if normalization_mode == "lufs":
        normalization_summary = f"LUFS ({target_lufs:g} LUFS / {true_peak:g} dBTP)"
    elif normalization_mode == "peak":
        normalization_summary = f"PEAK ({target_peak_dbfs:g} dBFS, boost only)"
    else:
        normalization_summary = "OFF"

    print()
    print("PATCH DONE")
    print(f"  Stock replaced: {len(stock_replacements)}/8")
    print(f"  Custom added: {len(custom_replacements)}")
    print(f"  Total patched: {len(replacements)}")
    print(f"  Normalization: {normalization_summary}")
    print(f"  Metadata: {'ON' if fetch_metadata else 'OFF'}")
    print(f"  Playlists: {playlist_mode.upper()}")
    print()
    print(f"  {out_sharedassets}")
    print(f"  {out_resources}")
    print(f"  {out_bank}")
    print(f"  {manifest_path}")
    print(f"  {report_path}")
    print()
    print("Copy output\\Heatwarped_Data to the game folder and overwrite when asked.")

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Heatwarped music patcher"
    )
    p.add_argument("--master", type=Path, default=DEFAULT_INPUT_DIR / "Master.bank")
    p.add_argument("--sharedassets", type=Path, default=DEFAULT_SHAREDASSETS)
    p.add_argument("--resources", type=Path, default=DEFAULT_RESOURCES)
    p.add_argument("--tracks", type=Path, default=DEFAULT_TRACKS_DIR)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--tools", type=Path, default=DEFAULT_TOOLS_DIR)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--playlists",
        choices=("stock", "full"),
        default=None,
        help="Override playlist mode for this run",
    )
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    print(f"Heatwarped Music Patcher v{APP_VERSION}")
    print(f"Input: {DEFAULT_INPUT_DIR}")

    try:
        # Ordre du pre-check : tools -> inputs/tracks -> config -> patch.
        ffmpeg, ogg_tool = check_tools(args.tools)
        input_replacements = check_patch_files(
            args.master, args.sharedassets, args.resources, args.tracks
        )

        config = load_config(args.config)
        if args.playlists is not None:
            config["playlist_mode"] = args.playlists
        patch(
            args.master,
            args.sharedassets,
            args.resources,
            input_replacements,
            args.output,
            ffmpeg,
            ogg_tool,
            config,
        )
        return 0
    except PatcherError as exc:
        print("\nERROR:", exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


def run() -> int:
    code = 1
    try:
        code = main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except BaseException:
        traceback.print_exc()
        code = 1
    finally:
        if getattr(sys, "frozen", False):
            try:
                input("\nPress Enter to close...")
            except (EOFError, KeyboardInterrupt):
                pass
    return code


if __name__ == "__main__":
    raise SystemExit(run())
