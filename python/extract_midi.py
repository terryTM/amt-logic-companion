#!/usr/bin/env python3
"""Extract MIDI note data from Logic Pro .logicx ProjectData files.

Usage:
    python extract_midi.py <project.logicx> [output.mid]

The script reads the proprietary binary ProjectData format, finds MIDI note
events, and writes a standard MIDI file (Format 1, 480 TPB).

Field mapping (reverse-engineered):
  - 32-byte events (no a7 separator):
    [dur_prev LE32] [90 00 00 00] [tick LE32] [00 flags b14 velocity]
    [pitch 00 00 01] [00 00 00 00] [00 00 00 89] [00 00 00 00]
  - 48-byte events (with a7 separator between each):
    Same 32-byte layout + 16-byte a7 separator block

  Internal ticks use 960 TPB (2x the standard 480 TPB MIDI resolution).
  Base tick offset is typically 38400 (= 20 bars lead-in at 960 TPB, 4/4).

Limitations:
  - May include a small number of false-positive events (~2-4 extra notes)
    that exist in the binary but not in the musical content.
  - Only extracts note-on/note-off events; no CC, pitch bend, or sysex.
  - Tested on Logic Pro 10.x and 12.x projects.
"""

import struct
import subprocess
import sys
import os
from pathlib import Path


def read_metadata(logicx_path: str) -> dict:
    """Read tempo, key, time signature from MetaData.plist."""
    plist_path = os.path.join(logicx_path, "Alternatives", "000", "MetaData.plist")
    meta = {"tempo": 120, "key": "C", "key_sf": 0, "key_mode": 0,
            "time_num": 4, "time_den": 4}
    try:
        result = subprocess.run(
            ["plutil", "-p", plist_path],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if '"BeatsPerMinute"' in line:
                meta["tempo"] = float(line.split("=>")[1].strip())
            elif '"SongKey"' in line:
                meta["key"] = line.split("=>")[1].strip().strip('"')
            elif '"SignatureKey"' in line:
                meta["key_sf"] = int(line.split("=>")[1].strip())
            elif '"SongGenderKey"' in line:
                val = line.split("=>")[1].strip().strip('"')
                meta["key_mode"] = 0 if val == "major" else 1
            elif '"SongSignatureNumerator"' in line:
                meta["time_num"] = int(line.split("=>")[1].strip())
            elif '"SongSignatureDenominator"' in line:
                meta["time_den"] = int(line.split("=>")[1].strip())
    except Exception:
        pass
    return meta


def find_evsq_chunks(data: bytes) -> list[tuple[int, int, int]]:
    """Find all EvSq (qSvE) chunks and their data boundaries.

    Returns list of (header_offset, data_start, data_end).
    """
    chunks = []
    idx = 0
    while True:
        idx = data.find(b'qSvE', idx)
        if idx == -1:
            break
        if idx + 48 <= len(data):
            data_size = struct.unpack_from('<I', data, idx + 28)[0]
            base_tick = struct.unpack_from('<I', data, idx + 40)[0]
            if 0 < data_size < 1_000_000 and base_tick < 1_000_000:
                data_start = idx + 32
                data_end = idx + 48 + data_size
                chunks.append((idx, data_start, min(data_end, len(data))))
        idx += 1
    return chunks


def find_note_events_in_range(data: bytes, start: int, end: int
                               ) -> list[tuple[int, int, int, int]]:
    """Find all 0x90 note-on events within a byte range.

    Returns list of (file_offset, internal_tick, pitch, velocity).
    """
    events = []
    for i in range(start, min(end, len(data)) - 32):
        if (data[i + 4] == 0x90 and data[i + 5] == 0
                and data[i + 6] == 0 and data[i + 7] == 0):
            tick = struct.unpack_from('<I', data, i + 8)[0]
            if tick < 1_000_000:
                pitch = data[i + 16]
                vel = data[i + 15]
                if 0 < pitch <= 127 and 0 < vel <= 127:
                    events.append((i, tick, pitch, vel))
    return events


def detect_event_spacing(events: list) -> int:
    """Detect whether events are 32-byte or 48-byte spaced."""
    if len(events) < 2:
        return 32
    spacings = [events[i][0] - events[i - 1][0]
                for i in range(1, min(10, len(events)))]
    avg = sum(spacings) / len(spacings)
    return 48 if avg > 40 else 32


def extract_notes_with_duration(data: bytes, events: list, spacing: int
                                 ) -> list[tuple[int, int, int, int]]:
    """Extract (tick, pitch, velocity, duration) from events.

    Duration of event N is in bytes 0-3 of the next event/separator block.
    For 32-byte format: duration is at offset+32.
    For 48-byte format: duration is at offset+32 (the a7 separator block).
    """
    notes = []
    for idx, (offset, tick, pitch, vel) in enumerate(events):
        dur_offset = offset + 32
        if dur_offset + 4 <= len(data):
            dur_raw = struct.unpack_from('<I', data, dur_offset)[0]
            if dur_raw > 100_000:
                dur_raw = 0
        else:
            dur_raw = 0
        notes.append((tick, pitch, vel, dur_raw))
    return notes


def list_evsq_regions(data: bytes) -> list[dict]:
    """List all EvSq regions with track/sub IDs and note counts.

    Returns list of dicts with keys: track_id, sub_id, offset, data_size, notes, base_tick.
    """
    regions = []
    idx = 0
    while True:
        idx = data.find(b'qSvE', idx)
        if idx == -1:
            break
        if idx + 48 <= len(data):
            track_id = struct.unpack_from('<H', data, idx + 6)[0]
            sub_id = struct.unpack_from('<I', data, idx + 8)[0]
            data_size = struct.unpack_from('<I', data, idx + 28)[0]
            base_tick = struct.unpack_from('<I', data, idx + 40)[0]
            if 0 < data_size < 1_000_000 and base_tick < 1_000_000:
                data_start = idx + 32
                data_end = idx + 48 + data_size
                events = find_note_events_in_range(data, data_start, min(data_end, len(data)))
                if events:
                    regions.append({
                        'track_id': track_id,
                        'sub_id': sub_id,
                        'offset': idx,
                        'data_size': data_size,
                        'notes': len(events),
                        'base_tick': base_tick,
                    })
        idx += 1
    return regions


def extract_midi_from_projectdata(data: bytes, track_id: int = None,
                                   sub_id: int = None, region_index: int = None
                                   ) -> list[tuple[int, int, int, int]]:
    """Main extraction: find note events and convert to MIDI format.

    Args:
        data: Raw ProjectData bytes.
        track_id: If set, only consider EvSq chunks with this track ID.
        sub_id: If set, only consider the EvSq chunk with this exact sub ID.
        region_index: If set, pick the Nth (0-based) region with notes
                      (after track_id filtering). Default: largest region.

    Returns list of (midi_tick, pitch, velocity, midi_duration) at 480 TPB.
    """
    evsq_chunks = find_evsq_chunks(data)

    candidates = []

    for header_off, data_start, data_end in evsq_chunks:
        # Filter by track_id if specified
        if track_id is not None:
            chunk_track = struct.unpack_from('<H', data, header_off + 6)[0]
            if chunk_track != track_id:
                continue

        # Filter by sub_id if specified
        if sub_id is not None:
            chunk_sub = struct.unpack_from('<I', data, header_off + 8)[0]
            if chunk_sub != sub_id:
                continue

        events = find_note_events_in_range(data, data_start, data_end)
        if len(events) < 3:
            continue

        base_tick = struct.unpack_from('<I', data, header_off + 40)[0]
        if base_tick > 1_000_000:
            base_tick = 38400

        spacing = detect_event_spacing(events)
        notes = extract_notes_with_duration(data, events, spacing)
        candidates.append((notes, base_tick))

    if not candidates:
        return []

    # Select which region to use
    if region_index is not None and region_index < len(candidates):
        best_notes, best_base = candidates[region_index]
    else:
        # Pick the one with the most notes
        best_notes, best_base = max(candidates, key=lambda c: len(c[0]))

    # Convert to MIDI ticks (480 TPB)
    # Internal ticks are at 960 TPB (2x), with base offset
    midi_notes = []
    for tick, pitch, vel, dur_raw in best_notes:
        midi_tick = (tick - best_base) // 2
        midi_dur = dur_raw // 2
        if midi_tick < 0:
            midi_tick = 0
        midi_notes.append((midi_tick, pitch, vel, midi_dur))

    return midi_notes


def write_varlen(val: int) -> bytes:
    """Encode a variable-length quantity for MIDI."""
    result = []
    result.append(val & 0x7F)
    val >>= 7
    while val:
        result.append((val & 0x7F) | 0x80)
        val >>= 7
    return bytes(reversed(result))


def write_midi(notes: list[tuple[int, int, int, int]],
               filename: str, meta: dict, track_name: str = ""):
    """Write a standard MIDI file (Format 1, 480 TPB)."""
    tpb = 480
    track_data_list = []

    # Track 0: tempo, key, time signature
    tb = bytearray()
    name = track_name or Path(filename).stem
    name_bytes = name.encode('ascii', errors='replace')
    tb += b'\x00\xFF\x03' + write_varlen(len(name_bytes)) + name_bytes

    # Tempo
    uspb = int(60_000_000 / meta["tempo"])
    tb += b'\x00\xFF\x51\x03' + struct.pack('>I', uspb)[1:]

    # Key signature
    sf = meta.get("key_sf", 0)
    # Convert Logic's key index to MIDI sharp/flat count
    # Logic uses: 0=C, 1=G, 2=D, ... 7=F#/Gb, 8=Db, ... 11=F
    key_map = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6,
               7: -6, 8: -5, 9: -4, 10: -3, 11: -1}
    midi_sf = key_map.get(sf, 0)
    tb += b'\x00\xFF\x59\x02' + struct.pack('bB', midi_sf, meta.get("key_mode", 0))

    # Time signature
    num = meta.get("time_num", 4)
    den = meta.get("time_den", 4)
    den_pow = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}.get(den, 2)
    tb += b'\x00\xFF\x58\x04' + bytes([num, den_pow, 24, 8])

    tb += b'\x00\xFF\x2F\x00'
    track_data_list.append(tb)

    # Track 1: notes
    events = []
    for tick, pitch, vel, dur in notes:
        events.append((tick, 0x90, pitch, vel))
        events.append((tick + dur, 0x80, pitch, 0))
    events.sort(key=lambda e: (e[0], e[1] == 0x90))  # note-offs before note-ons

    nb = bytearray()
    nb += b'\x00\xFF\x03\x00'  # empty track name
    prev_tick = 0
    for tick, status, note, vel in events:
        delta = max(0, tick - prev_tick)
        nb += write_varlen(delta)
        nb += bytes([status, note, vel])
        prev_tick = tick
    nb += b'\x00\xFF\x2F\x00'
    track_data_list.append(nb)

    with open(filename, 'wb') as f:
        f.write(b'MThd')
        f.write(struct.pack('>I', 6))
        f.write(struct.pack('>HHH', 1, len(track_data_list), tpb))
        for td in track_data_list:
            f.write(b'MTrk')
            f.write(struct.pack('>I', len(td)))
            f.write(td)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract MIDI from Logic Pro .logicx files")
    parser.add_argument("logicx", help="Path to .logicx project")
    parser.add_argument("output", nargs="?", help="Output .mid file path")
    parser.add_argument("--list", action="store_true", help="List all MIDI regions and exit")
    parser.add_argument("--track-id", type=int, default=None,
                        help="Internal track ID to extract from")
    parser.add_argument("--region", type=int, default=None,
                        help="0-based region index (after track filtering)")
    args = parser.parse_args()

    logicx_path = args.logicx

    # Read ProjectData
    project_data_path = os.path.join(logicx_path, "Alternatives", "000", "ProjectData")
    if not os.path.exists(project_data_path):
        print(f"Error: {project_data_path} not found")
        sys.exit(1)

    with open(project_data_path, 'rb') as f:
        data = f.read()

    # Read metadata
    meta = read_metadata(logicx_path)

    # List mode
    if args.list:
        print(f"Reading: {project_data_path} ({len(data)} bytes)")
        print(f"Tempo: {meta['tempo']} BPM, Key: {meta['key']}, "
              f"Time: {meta['time_num']}/{meta['time_den']}")
        regions = list_evsq_regions(data)
        if not regions:
            print("No MIDI regions found")
            sys.exit(1)
        print(f"\n{'Idx':>3}  {'Track':>5}  {'Sub':>10}  {'Notes':>5}  {'Size':>6}")
        print("-" * 40)
        for i, r in enumerate(regions):
            print(f"{i:3d}  {r['track_id']:5d}  0x{r['sub_id']:08X}  {r['notes']:5d}  {r['data_size']:6d}")
        sys.exit(0)

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        base = Path(logicx_path).stem
        suffix = ""
        if args.track_id is not None:
            suffix += f"_t{args.track_id}"
        if args.region is not None:
            suffix += f"_r{args.region}"
        output_path = str(Path(logicx_path).parent / f"{base}_extracted{suffix}.mid")

    print(f"Reading: {project_data_path} ({len(data)} bytes)")
    print(f"Tempo: {meta['tempo']} BPM, Key: {meta['key']}, "
          f"Time: {meta['time_num']}/{meta['time_den']}")

    # Extract MIDI notes
    notes = extract_midi_from_projectdata(data, track_id=args.track_id,
                                           region_index=args.region)

    if not notes:
        print("No MIDI notes found in ProjectData")
        sys.exit(1)

    # Summary
    note_names = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
    pitches = sorted(set(n[1] for n in notes))
    min_p, max_p = min(pitches), max(pitches)
    max_tick = max(n[0] + n[3] for n in notes)
    bars = max_tick / (480 * meta["time_num"])

    print(f"Extracted: {len(notes)} notes, ~{bars:.1f} bars")
    print(f"Pitch range: {note_names[min_p%12]}{min_p//12-1} to "
          f"{note_names[max_p%12]}{max_p//12-1} ({min_p}-{max_p})")
    print(f"Velocity range: {min(n[2] for n in notes)}-{max(n[2] for n in notes)}")

    # Write MIDI
    track_name = Path(logicx_path).stem
    write_midi(notes, output_path, meta, track_name)
    print(f"Wrote: {output_path} ({os.path.getsize(output_path)} bytes)")


if __name__ == "__main__":
    main()
