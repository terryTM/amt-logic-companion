#!/usr/bin/env python3
"""Inject MIDI note data into Logic Pro .logicx ProjectData files.

Usage:
    python inject_midi.py <input.mid> <project.logicx> [--track-id N] [--evsq-index N]

Reads a standard MIDI file and writes the note events into the specified
EvSq chunk of the Logic Pro project's ProjectData binary.

The script modifies the ProjectData file in-place (after creating a backup).
"""

import struct
import shutil
import subprocess
import sys
import os
import time
import uuid
import json
from pathlib import Path


def create_track_via_ui(logicx_path: str):
    """Create a new Software Instrument track in Logic Pro via UI automation.

    Uses AppleScript to click Track > New Software Instrument Track, then saves.
    Logic Pro must be open with the target project.
    """
    # Bring Logic Pro to front and open the project if needed
    app_name = None
    result = subprocess.run(
        ['osascript', '-e',
         'tell application "System Events" to name of every process whose background only is false'],
        capture_output=True, text=True)
    for name in result.stdout.strip().split(', '):
        if 'Logic' in name:
            app_name = name
            break

    if not app_name:
        print("Error: Logic Pro is not running")
        sys.exit(1)

    print(f"  Using UI automation with: {app_name}")

    # Snapshot ProjectData before
    pd_path = os.path.join(logicx_path, "Alternatives", "000", "ProjectData")
    before_size = os.path.getsize(pd_path)
    before_mtime = os.path.getmtime(pd_path)

    # Create new track via menu
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            click menu item "New Software Instrument Track" of menu 1 of menu bar item "Track" of menu bar 1
        end tell
    end tell
    '''
    subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    print("  Clicked 'New Software Instrument Track'")
    time.sleep(1)

    # Save the project
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            keystroke "s" using command down
        end tell
    end tell
    '''
    subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    print("  Sent Cmd+S to save")

    # Wait for the file to be updated
    for _ in range(30):
        time.sleep(0.5)
        new_mtime = os.path.getmtime(pd_path)
        new_size = os.path.getsize(pd_path)
        if new_mtime > before_mtime and new_size != before_size:
            print(f"  ProjectData updated: {before_size} -> {new_size} bytes")
            return True

    print("  Warning: ProjectData did not change after save")
    return False


def parse_midi_file(filepath: str) -> tuple[list[tuple[int, int, int, int]], int]:
    """Parse a standard MIDI file and return note events.

    Returns (notes, tpb) where notes is list of (tick, pitch, velocity, duration)
    at the file's native TPB resolution.
    """
    with open(filepath, 'rb') as f:
        data = f.read()

    assert data[:4] == b'MThd', "Not a MIDI file"
    header_len = struct.unpack('>I', data[4:8])[0]
    fmt, ntracks, tpb = struct.unpack('>HHH', data[8:14])

    pos = 8 + header_len
    all_notes = []

    for t in range(ntracks):
        assert data[pos:pos + 4] == b'MTrk'
        track_len = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        track_end = pos + 8 + track_len

        p = pos + 8
        abs_tick = 0
        running_status = 0
        notes_on = {}

        while p < track_end:
            # Variable-length delta
            delta = 0
            while True:
                b = data[p]; p += 1
                delta = (delta << 7) | (b & 0x7F)
                if not (b & 0x80):
                    break
            abs_tick += delta

            if data[p] & 0x80:
                status = data[p]; p += 1
            else:
                status = running_status

            if status == 0xFF:
                meta_type = data[p]; p += 1
                length = 0
                while True:
                    b = data[p]; p += 1
                    length = (length << 7) | (b & 0x7F)
                    if not (b & 0x80):
                        break
                p += length
                if meta_type == 0x2F:
                    break
            elif 0x80 <= status <= 0xEF:
                running_status = status
                msg_type = status & 0xF0
                if msg_type in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    d1 = data[p]; d2 = data[p + 1]; p += 2
                    if msg_type == 0x90 and d2 > 0:
                        notes_on.setdefault(d1, []).append((abs_tick, d2))
                    elif msg_type == 0x80 or (msg_type == 0x90 and d2 == 0):
                        if d1 in notes_on and notes_on[d1]:
                            on_tick, vel = notes_on[d1].pop(0)
                            all_notes.append((on_tick, d1, vel, abs_tick - on_tick))
                elif msg_type in (0xC0, 0xD0):
                    p += 1
            elif status in (0xF0, 0xF7):
                length = 0
                while True:
                    b = data[p]; p += 1
                    length = (length << 7) | (b & 0x7F)
                    if not (b & 0x80):
                        break
                p += length

        pos = track_end

    all_notes.sort(key=lambda n: (n[0], n[1]))
    return all_notes, tpb


def build_evsq_events(notes: list[tuple[int, int, int, int]],
                      base_tick: int, source_tpb: int) -> bytes:
    """Build the binary event data for an EvSq chunk.

    Args:
        notes: List of (tick, pitch, velocity, duration) at source_tpb resolution.
        base_tick: The base tick offset (typically 38400 for bar 1).
        source_tpb: TPB of the source MIDI (e.g. 480).

    Returns:
        Raw bytes of event data for the EvSq chunk, starting with a 16-byte
        sentinel, followed by 32-byte note events, then a 20-byte terminator.
    """
    # Convert from source TPB to internal 960 TPB
    scale = 960 / source_tpb

    result = bytearray()

    # Write 16-byte sentinel (region header) at the start
    # Uses 0xC0 marker (program change) so Logic doesn't display it as a note
    sentinel = bytearray(16)
    sentinel[4] = 0xC0          # marker: program change (not 0x90 note-on)
    struct.pack_into('<I', sentinel, 8, base_tick)  # tick = base_tick
    sentinel[15] = 0x04         # flag byte
    result.extend(sentinel)

    for i, (tick, pitch, vel, dur) in enumerate(notes):
        internal_tick = int(tick * scale) + base_tick
        internal_dur = int(dur * scale)

        # dur_prev: duration of the PREVIOUS note event (0 for first)
        if i == 0:
            dur_prev = 0
        else:
            prev_dur = int(notes[i - 1][3] * scale)
            dur_prev = prev_dur

        event = bytearray(32)
        # +0-3: dur_prev (LE32)
        struct.pack_into('<I', event, 0, dur_prev)
        # +4-7: 0x90 note-on marker
        event[4] = 0x90
        # +8-11: absolute tick (LE32)
        struct.pack_into('<I', event, 8, internal_tick)
        # +12-13: 0x00 0x00 (sub-tick / internal flags, varies in Logic)
        # +14: 0x00 (internal metadata, varies per-note in Logic)
        # +15: velocity
        event[15] = vel
        # +16: pitch
        event[16] = pitch
        # +17-18: 0x00 0x00
        # +19: 0x01 (channel/class flag)
        event[19] = 0x01
        # +20-23: 0x00000000
        # +24-27: 0x89000000 (constant, stored as 00 00 00 89)
        event[27] = 0x89
        # +28-31: 0x00000000

        result.extend(event)

    # Terminator: dur of last event + sentinel
    terminator = bytearray(20)
    if notes:
        last_dur = int(notes[-1][3] * scale)
        struct.pack_into('<I', terminator, 0, last_dur)
    # Sentinel: F1 marker
    terminator[4] = 0xF1
    # Sentinel tick: 0x3FFFFFFF
    struct.pack_into('<I', terminator, 8, 0x3FFFFFFF)
    # +12-19: zeros (already zeroed)
    result.extend(terminator)

    return bytes(result)


def activate_region_metadata(data: bytearray, track_id: int,
                              target_sub_key: int,
                              chunks: list[dict]) -> bool:
    """Flip the 0x80 'pristine' flag in the region index entry.

    When a MIDI region is first created by Logic, its entry in the index chunk
    (sub=0x00040000 or sub=0x00080000) has entry[15]=0x80 meaning 'never written'.
    Logic won't display notes unless this flag is set to 0x81.

    Entry layout (80 bytes each, starting at data region offset 0):
      entry[15]: flag byte (0x80=pristine, 0x81=has content)
      entry[32:36]: referenced sub_key as LE32 (e.g., 0x1C for sub=0x001C0000)

    Args:
        data: Mutable ProjectData bytes.
        track_id: Track ID of the target region.
        target_sub_key: Sub key of the target region (sub_id >> 16, e.g. 0x1C).
        chunks: List of EvSq chunk dicts from find_evsq_chunks().

    Returns True if a flag was flipped.
    """
    activated = False
    for c in chunks:
        if c['track_id'] != track_id:
            continue
        if c['sub_id'] not in (0x00040000, 0x00080000):
            continue

        # Data region starts at chunk_offset + 36, contains N x 80-byte entries
        # + 16-byte trailer.
        data_start = c['offset'] + 36
        data_end = c['offset'] + 36 + c['data_size']

        pos = data_start
        while pos + 80 <= data_end:
            ref_sub = struct.unpack_from('<I', data, pos + 32)[0]
            if ref_sub == target_sub_key:
                old_flag = data[pos + 15]
                if old_flag != 0x81:
                    data[pos + 15] = 0x81
                    print(f"  Activated EvSq index flag for sub=0x{target_sub_key:04X} "
                          f"(0x{old_flag:02X}->0x81) "
                          f"in index 0x{c['sub_id']:08X}")
                    activated = True
                break
            pos += 80

    return activated


def ensure_display_index_entry(data: bytearray, track_id: int,
                                target_sub_key: int,
                                chunks: list[dict]) -> tuple[bytearray, bool]:
    """Ensure the display index (sub=0x00040000) has an entry for target_sub_key.

    When a MIDI region is created, Logic normally pre-populates an 80-byte entry
    in the sub=0x00040000 display index with flag=0x80 (pristine). However, some
    projects (e.g., freshly created single-track projects) have an empty display
    index (ds=16, no entries). In that case, we must INSERT an 80-byte entry to
    make notes visible in the arrange view.

    The 80-byte entry template (from reference Logic Pro binary):
      [0:4]   = 0x20000000 (LE32 = 32)
      [4:6]   = 0x0087
      [13]    = 0x05 (last-entry flag)
      [15]    = 0x81 (has-content flag)
      [16:20] = chunk_id reference (0x58 default)
      [20]    = sequence number (0x01 default)
      [24:28] = 0x89000000
      [28:32] = 0xFFFFFF3F
      [32:36] = target_sub_key (LE32)
      [39]    = 0x88
      [48:52] = 0x00060000
      [52:56] = 0x0000068A
      [71]    = 0x88

    Args:
        data: Mutable ProjectData bytes.
        track_id: Track ID of the target region.
        target_sub_key: Sub key (sub_id >> 16, e.g. 0x1C).
        chunks: List of EvSq chunk dicts from find_evsq_chunks().

    Returns (possibly_new_data, True if entry was inserted).
    """
    # Find the display index chunk (sub=0x00040000) for this track
    display_chunk = None
    for c in chunks:
        if c['track_id'] == track_id and c['sub_id'] == 0x00040000:
            display_chunk = c
            break

    if display_chunk is None:
        return data, False

    # Check if entry already exists
    data_start = display_chunk['offset'] + 36
    data_end = display_chunk['offset'] + 36 + display_chunk['data_size']
    pos = data_start
    while pos + 80 <= data_end:
        ref_sub = struct.unpack_from('<I', data, pos + 32)[0]
        if ref_sub == target_sub_key:
            return data, False  # Entry already exists
        pos += 80

    # Entry doesn't exist — need to insert one
    # Count existing entries to determine track number (1-based)
    num_existing = 0
    pos = data_start
    last_entry_pos = None
    while pos + 80 <= data_end:
        last_entry_pos = pos
        num_existing += 1
        pos += 80
    track_number = num_existing + 1  # 1-based track number for the new entry

    # Build 80-byte entry from reference template
    entry = bytearray(80)
    struct.pack_into('<I', entry, 0, 0x20)
    entry[5] = 0x87
    entry[13] = 0x05  # last-entry flag (this is the new last entry)
    entry[15] = 0x81  # has-content flag
    entry[16] = 0x58
    entry[20] = track_number  # 1-based track number
    entry[23] = 0x89
    entry[28:32] = b'\xff\xff\xff\x3f'
    struct.pack_into('<I', entry, 32, target_sub_key)
    entry[39] = 0x88
    entry[49] = 0x06
    entry[54] = 0x06
    entry[55] = 0x8a
    entry[71] = 0x88

    # Mark previous last entry as non-last (0x04 instead of 0x05)
    if last_entry_pos is not None:
        data[last_entry_pos + 13] = 0x04

    # Insert entry before the 16-byte terminator at the end
    # The terminator is the last 16 bytes of the data region
    insert_pos = data_end - 16  # Before terminator (if ds=16, this is data_start)
    if display_chunk['data_size'] <= 16:
        insert_pos = data_start  # Empty chunk: insert at very start, before terminator

    data = data[:insert_pos] + bytes(entry) + data[insert_pos:]

    # Update data_size in the chunk header (+28)
    new_data_size = display_chunk['data_size'] + 80
    struct.pack_into('<I', data, display_chunk['offset'] + 28, new_data_size)

    print(f"  Inserted display index entry for sub=0x{target_sub_key:04X} "
          f"in sub=0x00040000 (ds: {display_chunk['data_size']} -> {new_data_size})")

    return data, True


def activate_qesm_region(data: bytearray, track_id: int,
                          target_sub_key: int,
                          region_length_units: int = 0) -> bool:
    """Mark a region as active in its qeSM metadata block.

    When Logic adds notes to an empty region, it updates 4 fields in the qeSM
    entry for that region's sub_key. Without these updates, Logic won't display
    the notes even though the EvSq data is correctly written.

    The qeSM structure has a variable-length name field at +52. Field positions
    are computed relative to the end of the name field:
      name_field_end + 61:  region length (LE16, units of 32 ticks at 960 TPB)
      name_field_end + 75:  reference value (already set, used as source)
      name_field_end + 123: region display marker (copy from +75)
      name_field_end + 127: flags (increment by 1, e.g. 0x04 → 0x05)
      name_field_end + 172: self-reference sub_key (set to target_sub_key)
      name_field_end + 192: has-content flag (set to 1)

    Args:
        data: Mutable ProjectData bytes.
        track_id: Track ID of the target region.
        target_sub_key: Sub key (sub_id >> 16, e.g. 0x1C).
        region_length_units: Region display length in units of 32 ticks at
            960 TPB. 120 = 1 bar at 4/4. 0 = don't set.

    Returns True if fields were updated.
    """
    # Find the qeSM for this sub_key
    idx = 0
    while True:
        idx = data.find(b'qeSM', idx)
        if idx == -1:
            break
        if idx + 54 <= len(data):
            tid = struct.unpack_from('<H', data, idx + 6)[0]
            sub = struct.unpack_from('<H', data, idx + 10)[0]
            if tid == track_id and sub == target_sub_key:
                # Found it — compute name field end
                name_len = struct.unpack_from('<H', data, idx + 52)[0]
                # Name field: 2 bytes (length) + name_len bytes + pad to even
                name_field_end = 54 + name_len + (name_len % 2)

                # Verify the reference value exists at name_field_end + 75
                ref_off = idx + name_field_end + 75
                if ref_off >= len(data):
                    break
                ref_value = data[ref_off]

                # Patch the 4 fields
                off_marker = idx + name_field_end + 123
                off_flags = idx + name_field_end + 127
                off_refsub = idx + name_field_end + 172
                off_hascontent = idx + name_field_end + 192

                if off_hascontent + 4 > len(data):
                    break

                data[off_marker] = ref_value
                data[off_flags] = data[off_flags] + 1
                struct.pack_into('<I', data, off_refsub, target_sub_key)
                struct.pack_into('<I', data, off_hascontent, 1)

                # Set region display length
                if region_length_units > 0:
                    off_region_len = idx + name_field_end + 61
                    if off_region_len + 2 <= len(data):
                        struct.pack_into('<H', data, off_region_len,
                                         region_length_units)
                        bars = region_length_units / 15
                        print(f"  Set region length: {region_length_units} "
                              f"units (~{bars:.1f} bars)")

                print(f"  Activated qeSM for sub=0x{target_sub_key:04X}: "
                      f"marker=0x{ref_value:02X}, flags=0x{data[off_flags]:02X}, "
                      f"ref=0x{target_sub_key:04X}, hasContent=1")
                return True
        idx += 1

    return False


def _find_note_sub_keys(data: bytes) -> dict[int, set[int]]:
    """Find note region sub_keys by reading display + playback indexes.

    Reads both the display index (sub=0x00040000) and playback index
    (sub=0x00080000) to find all sub_keys that reference note regions.

    Returns {track_id: {sub_key1, sub_key2, ...}}.
    """
    result = {}
    idx = 0
    while True:
        idx = data.find(b'qSvE', idx)
        if idx == -1:
            break
        if idx + 48 <= len(data):
            track_id = struct.unpack_from('<H', data, idx + 6)[0]
            sub_id = struct.unpack_from('<I', data, idx + 8)[0]
            data_size = struct.unpack_from('<I', data, idx + 28)[0]
            if sub_id in (0x00040000, 0x00080000) and 0 < data_size < 1_000_000:
                if track_id not in result:
                    result[track_id] = set()
                pos = idx + 36
                end = idx + 36 + data_size
                while pos + 80 <= end:
                    ref_sub = struct.unpack_from('<I', data, pos + 32)[0]
                    if ref_sub > 0:
                        result[track_id].add(ref_sub)
                    pos += 80
        idx += 1
    return result


def find_evsq_chunks(data: bytes) -> list[dict]:
    """Find all EvSq chunks with their metadata."""
    # First, find which sub_keys are note regions via the playback index
    note_sub_keys = _find_note_sub_keys(data)

    chunks = []
    idx = 0
    while True:
        idx = data.find(b'qSvE', idx)
        if idx == -1:
            break
        if idx + 48 <= len(data):
            track_id = struct.unpack_from('<H', data, idx + 6)[0]
            sub_id = struct.unpack_from('<I', data, idx + 8)[0]
            data_size = struct.unpack_from('<I', data, idx + 28)[0]
            if 0 < data_size < 1_000_000:
                # data_size counts from +36 to chunk end
                # Layout: 12 bytes prefix (from +36 to +47) + events (32 each) + terminator (20)
                # num_events = (data_size - 12 - 20) / 32
                event_bytes = max(0, data_size - 12 - 20)
                num_events = event_bytes // 32
                # A note chunk is one whose sub_key is referenced in the
                # playback index (sub=0x00080000). Falls back to the old
                # heuristic if no index is found for this track.
                sub_key = sub_id >> 16
                if track_id in note_sub_keys:
                    is_note_chunk = (sub_key in note_sub_keys[track_id]
                                     and (sub_id & 0x0000FFFF) == 0)
                else:
                    is_note_chunk = (sub_id >= 0x00140000 and sub_id <= 0x00FF0000
                                     and (sub_id & 0x0000FFFF) == 0
                                     and track_id != 14)
                chunks.append({
                    'offset': idx,
                    'track_id': track_id,
                    'sub_id': sub_id,
                    'data_size': data_size,
                    'num_events': num_events,
                    'is_note_chunk': is_note_chunk,
                    'chunk_size': 36 + data_size,
                })
        idx += 1
    return chunks


# ---- Track creation helpers ----

def generate_uuid_bytes():
    """Generate a new UUID in the format Logic uses (big-endian mixed)."""
    return uuid.uuid4().bytes


def find_ocua_entries(data):
    """Find all OCuA entries and their offsets/sizes."""
    entries = []
    idx = 0
    while True:
        idx = data.find(b'OCuA', idx)
        if idx == -1:
            break
        entries.append(idx)
        idx += 1
    return entries


def expand_ocua_entry(data, ocua_entries, new_uuid):
    """Expand an OCuA channel strip entry for a new instrument track.

    When Logic Pro adds a track, it:
    1. Moves OCuA[target]'s old data into the next stub (preserving it)
    2. Clones the largest expanded Inst entry into OCuA[target]
    3. Updates the header entry's counter fields

    Returns (new_data, ocua_index, size_delta) or None.
    """
    # Find entry sizes and types
    entry_info = []
    for i in range(len(ocua_entries) - 1):
        start = ocua_entries[i]
        size = ocua_entries[i + 1] - start
        ctype = data[start + 40] if start + 41 <= len(data) else 0
        entry_info.append({'offset': start, 'size': size, 'type': ctype, 'index': i})

    # Find the first expanded Inst entry (type 0x43) to clone from
    inst_source = None
    for e in entry_info:
        if e['type'] == 0x43 and e['size'] > 237:
            inst_source = e
            break

    if inst_source is None:
        print("  Warning: No expanded Inst OCuA entry to clone, skipping")
        return None

    # Find the target: the smallest expanded Inst entry (Logic expands the last/smallest)
    target = None
    for e in entry_info:
        if e['type'] == 0x43 and e['size'] > 237 and e != inst_source:
            if target is None or e['size'] < target['size']:
                target = e

    if target is None:
        # Try an Inst stub (237 bytes)
        for e in entry_info:
            if e['type'] == 0x43 and e['size'] == 237:
                target = e
                break
    if target is None:
        print("  OCuA: No Inst entry to expand")
        return None

    # Find the next entry after target (the stub that will receive target's old data)
    next_entry = None
    if target['index'] + 1 < len(entry_info):
        next_entry = entry_info[target['index'] + 1]

    result = bytearray(data)

    # Step 1: Move target's old data into next_entry (if next_entry is a stub)
    if next_entry and next_entry['size'] == 237 and target['size'] > 237:
        old_target_data = bytearray(data[target['offset']:
                                         target['offset'] + target['size']])
        # Update the moved data's +14 index to next_entry's index
        next_idx14 = struct.unpack_from('<I', data, next_entry['offset'] + 14)[0]
        struct.pack_into('<I', old_target_data, 14, next_idx14)
        # Update +42 sub-index: increment by 1
        old_sub42 = struct.unpack_from('<H', old_target_data, 42)[0]
        struct.pack_into('<H', old_target_data, 42, old_sub42 + 1)
        # Also update internal index references (+102, +303, +591, +2548 if they exist)
        old_idx14 = struct.unpack_from('<I', data, target['offset'] + 14)[0]
        for ref_off in [102, 303, 591, 2548]:
            if ref_off + 4 <= len(old_target_data):
                ref_val = struct.unpack_from('<I', old_target_data, ref_off)[0]
                if ref_val == old_idx14:
                    struct.pack_into('<I', old_target_data, ref_off, next_idx14)
        # Replace next_entry with old target data
        ne_offset = next_entry['offset']
        ne_size = next_entry['size']
        result = (result[:ne_offset] + bytes(old_target_data)
                  + result[ne_offset + ne_size:])
        move_delta = len(old_target_data) - ne_size
        print(f"  OCuA: moved [{target['index']}] data to [{next_entry['index']}] "
              f"({ne_size} -> {len(old_target_data)} bytes)")
    else:
        move_delta = 0

    # Step 2: Clone source into target
    source_data = bytearray(data[inst_source['offset']:
                                  inst_source['offset'] + inst_source['size']])
    target_idx14 = struct.unpack_from('<I', data, target['offset'] + 14)[0]
    struct.pack_into('<I', source_data, 14, target_idx14)
    target_subidx = struct.unpack_from('<H', data, target['offset'] + 42)[0]
    struct.pack_into('<H', source_data, 42, target_subidx)
    source_data[241:257] = new_uuid

    entry_offset = target['offset']
    old_size = target['size']
    result = (result[:entry_offset] + bytes(source_data)
              + result[entry_offset + old_size:])
    expand_delta = len(source_data) - old_size

    # Step 3: Update header entry[0] counter fields (+78, +112, +120)
    header_off = entry_info[0]['offset']
    for counter_off in [78, 112, 120]:
        if header_off + counter_off + 4 <= len(result):
            old_val = struct.unpack_from('<I', result, header_off + counter_off)[0]
            struct.pack_into('<I', result, header_off + counter_off, old_val + 1)

    size_delta = expand_delta + move_delta
    print(f"  OCuA: expanded entry [{target['index']}] at {entry_offset}: "
          f"{old_size} -> {len(source_data)} bytes (+{size_delta} total)")
    return result, target['index'], size_delta


def find_ivne_entries(data):
    """Find all ivnE-delimited track index entries."""
    positions = []
    idx = 0
    while True:
        idx = data.find(b'ivnE', idx)
        if idx == -1:
            break
        positions.append(idx)
        idx += 1
    return positions


def modify_track_index(data, new_uuid, track_name="Inst"):
    """Modify the track index array to add a new track entry.

    The track index is an array of ivnE-delimited entries. Empty entries are 298
    bytes with "(=Context Name)". When adding a track:
    1. First empty entry gets replaced with a populated entry (cloned from existing)
    2. A new 298-byte empty entry is inserted before the special (>298) entry
    3. Field values shift by one position in remaining empty entries
    4. Size fields in entries [8-10] and the special entry get +66 at offset +118

    Returns (new_data, size_delta, track_index_value) or None.
    """
    ivne_positions = find_ivne_entries(data)
    if len(ivne_positions) < 15:
        print("  Warning: Not enough ivnE entries found")
        return None

    # Build entry list: (offset, size, data)
    entries = []
    for i in range(len(ivne_positions) - 1):
        start = ivne_positions[i]
        end = ivne_positions[i + 1]
        entries.append({'offset': start, 'size': end - start, 'data': data[start:end]})

    # Find an existing populated entry (size ~511-513, not in system entries [0-10])
    # to clone as a template
    existing_populated = None
    for i in range(11, len(entries)):
        if 500 <= entries[i]['size'] <= 520:
            existing_populated = i
            break

    if existing_populated is None:
        # Fall back to external template
        POPULATED_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__),
                                                'track_index_entry_template.bin')
        if not os.path.exists(POPULATED_TEMPLATE_PATH):
            print("  Warning: No populated entry to clone and no template file")
            return None
        with open(POPULATED_TEMPLATE_PATH, 'rb') as f:
            populated_template = bytearray(f.read())
    else:
        # Clone the existing populated entry
        populated_template = bytearray(entries[existing_populated]['data'])

    # Find first empty entry (298 bytes with "(=Context Name)")
    first_empty = None
    for i, e in enumerate(entries):
        if e['size'] == 298 and b'(=Context Name)' in e['data']:
            first_empty = i
            break

    if first_empty is None:
        print("  Warning: No empty track index entry found")
        return None

    # Find end of array (last entry)
    last_entry_idx = len(entries) - 1
    last_entry_end = entries[last_entry_idx]['offset'] + entries[last_entry_idx]['size']

    print(f"  Track index: {len(entries)} entries, first empty=[{first_empty}], "
          f"cloned from=[{existing_populated}]")

    # Populate the template:
    # +10, +52: use first_empty's value minus 4, avoiding collision with existing
    first_empty_data = entries[first_empty]['data']
    first_empty_10 = struct.unpack_from('<H', first_empty_data, 10)[0]
    candidate_10 = first_empty_10 - 4

    existing_10_values = set()
    for i in range(11, len(entries)):
        if 500 <= entries[i]['size'] <= 520:
            existing_10_values.add(struct.unpack_from('<H', entries[i]['data'], 10)[0])

    if candidate_10 in existing_10_values:
        new_10 = first_empty_10
    else:
        new_10 = candidate_10
    struct.pack_into('<H', populated_template, 10, new_10)
    struct.pack_into('<H', populated_template, 52, new_10)

    # Patch track name at +196 (length byte at +194)
    name_bytes = track_name.encode('ascii')[:14]
    name_len = len(name_bytes)
    populated_template[196:196 + 14] = b'\x00' * 14
    populated_template[194] = name_len
    populated_template[196:196 + name_len] = name_bytes

    # Patch UUID at +496 (0x03 marker) and +497 (16-byte UUID)
    populated_template[496] = 0x03
    populated_template[497:513] = new_uuid[:16]

    # Build new empty entry (clone last empty in the array)
    last_empty_entry = None
    for i in range(len(entries) - 1, first_empty - 1, -1):
        if entries[i]['size'] == 298 and b'(=Context Name)' in entries[i]['data']:
            last_empty_entry = entries[i]
            break
    if last_empty_entry is None:
        last_empty_entry = entries[first_empty]
    new_empty = bytearray(last_empty_entry['data'][:298])
    # Set new empty's +10 to last entry's +10 + 4
    last_10 = struct.unpack_from('<H', entries[last_entry_idx]['data'], 10)[0]
    struct.pack_into('<H', new_empty, 10, last_10 + 4)

    # Replace first_empty with populated, append new empty at end of array
    fe_offset = entries[first_empty]['offset']
    fe_end = fe_offset + 298

    result = bytearray(data)
    result = (result[:fe_offset]
              + bytes(populated_template)
              + result[fe_end:last_entry_end]
              + bytes(new_empty)
              + result[last_entry_end:])

    # Update track count fields in file header
    old_count = struct.unpack_from('<H', result, 0x10E)[0]
    struct.pack_into('<H', result, 0x10E, old_count + 1)
    struct.pack_into('<H', result, 0x112, old_count + 1)
    print(f"  Track count: {old_count} -> {old_count + 1}")

    size_delta = (len(populated_template) - 298) + 298
    print(f"  Track index: replaced entry[{first_empty}] (298->{len(populated_template)}), "
          f"appended new empty at end (+{size_delta})")

    return result, size_delta, new_10


def insert_kart_entry(data, target_track, new_uuid, track_index_value):
    """Insert a 94-byte karT entry in the qeSM sub=0x0004 chain.

    The karT chain lives between the qeSM for sub=0x0004 and its EvSq.
    Each karT entry is 94 bytes and references a track by UUID.

    Returns (new_data, size_delta) or None.
    """
    # 94-byte karT template
    KART_TEMPLATE = bytearray(94)
    KART_TEMPLATE[0:4] = b'karT'
    KART_TEMPLATE[4:6] = struct.pack('<H', 6)  # version
    struct.pack_into('<H', KART_TEMPLATE, 6, target_track)  # track_id
    struct.pack_into('<H', KART_TEMPLATE, 10, 0x0004)  # sub_key
    KART_TEMPLATE[14:18] = b'\xFF\xFF\xFF\xFF'
    # +18: order index (will be patched)
    KART_TEMPLATE[22:26] = struct.pack('<I', 2)
    KART_TEMPLATE[26:28] = struct.pack('<H', 2)
    struct.pack_into('<H', KART_TEMPLATE, 28, 0x003A)
    KART_TEMPLATE[36] = 0x01
    KART_TEMPLATE[38] = 0x01  # reference
    struct.pack_into('<H', KART_TEMPLATE, 44, track_index_value)  # track index +10 value
    KART_TEMPLATE[60:76] = new_uuid[:16]

    # Find the qeSM for sub=0x0004 in the target track
    idx = 0
    qesm_sub4_offset = None
    while True:
        idx = data.find(b'qeSM', idx)
        if idx == -1:
            break
        if idx + 12 <= len(data):
            tid = struct.unpack_from('<H', data, idx + 6)[0]
            sub = struct.unpack_from('<H', data, idx + 10)[0]
            if tid == target_track and sub == 0x0004:
                qesm_sub4_offset = idx
                break
        idx += 1

    if qesm_sub4_offset is None:
        print("  Warning: qeSM sub=0x0004 not found, skipping karT insertion")
        return None

    # Find the EvSq for sub=0x00040000 after this qeSM
    evsq_offset = data.find(b'qSvE', qesm_sub4_offset)
    if evsq_offset == -1:
        print("  Warning: EvSq after qeSM sub=0x0004 not found")
        return None

    # Find the last karT entry before the EvSq
    # The karT chain is between qeSM and EvSq
    last_kart = None
    scan = qesm_sub4_offset
    while True:
        pos = data.find(b'karT', scan)
        if pos == -1 or pos >= evsq_offset:
            break
        last_kart = pos
        scan = pos + 1

    if last_kart is None:
        print("  Warning: No karT entries found in qeSM sub=0x0004 gap")
        return None

    # Count existing karT entries to set the order index
    kart_count = 0
    scan = qesm_sub4_offset
    while True:
        pos = data.find(b'karT', scan)
        if pos == -1 or pos >= evsq_offset:
            break
        kart_count += 1
        scan = pos + 1

    # Set order index (the new entry goes before the last karT)
    KART_TEMPLATE[18] = kart_count - 1  # 0-indexed, insert before last

    # Insert before the last karT (which is the short 36-byte terminator karT)
    insert_offset = last_kart
    result = data[:insert_offset] + bytes(KART_TEMPLATE) + data[insert_offset:]

    print(f"  karT: inserted 94-byte entry at {insert_offset} "
          f"(order={kart_count-1}, {kart_count} existing)")

    return result, 94


def update_json_drummer_state(data, new_uuid):
    """Add a new entry to the drummerModelTrackStates JSON.

    Returns (new_data, size_delta) or None.
    """
    json_start = data.find(b'{"drummerModelTrackStates"')
    if json_start == -1:
        print("  Warning: JSON drummer state not found, skipping")
        return None

    # Find matching braces
    depth = 0
    json_end = json_start
    for i in range(json_start, len(data)):
        if data[i:i + 1] == b'{':
            depth += 1
        elif data[i:i + 1] == b'}':
            depth -= 1
            if depth == 0:
                json_end = i + 1
                break

    old_json = data[json_start:json_end]

    try:
        parsed = json.loads(old_json)
    except json.JSONDecodeError:
        print("  Warning: Could not parse JSON drummer state")
        return None

    # Format UUID as Logic-style string: XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX
    uuid_hex = new_uuid.hex().upper()
    uuid_str = (f"{uuid_hex[0:8]}-{uuid_hex[8:12]}-{uuid_hex[12:16]}-"
                f"{uuid_hex[16:20]}-{uuid_hex[20:32]}")

    # Add default drummer state
    default_state = {
        "keepDrumKitWhenChangingDrummer": False,
        "selectedCharacterIdentifier": "Acoustic Drummer - Pop Rock",
        "isUsingProducerKit": False,
        "stateVersion": 3,
        "keepSettingsWhenChangingDrummer": False,
        "selectedPersistentCharacterTypeIdentifier": "Type_AcousticDrummerV2",
        "parametersWhereChangedAfterCharacterRecall": False,
    }

    parsed.setdefault('drummerModelTrackStates', {})[uuid_str] = default_state
    new_json = json.dumps(parsed, separators=(',', ':')).encode('ascii')

    result = data[:json_start] + new_json + data[json_end:]
    size_delta = len(new_json) - len(old_json)
    print(f"  JSON: added drummer state for {uuid_str} ({size_delta:+d} bytes)")

    return result, size_delta


def inject_midi(logicx_path: str, midi_path: str,
                evsq_index: int | None = None,
                track_id: int | None = None,
                new_track: bool = False):
    """Inject MIDI notes into a Logic Pro project file.

    Args:
        logicx_path: Path to the .logicx bundle.
        midi_path: Path to the source .mid file.
        evsq_index: Index of the note EvSq chunk to replace (0-based).
        track_id: Track ID to target (alternative to evsq_index).
        new_track: If True, always create a new note track instead of replacing.
    """
    project_data_path = os.path.join(
        logicx_path, "Alternatives", "000", "ProjectData")

    if not os.path.exists(project_data_path):
        print(f"Error: {project_data_path} not found")
        sys.exit(1)

    # Parse MIDI file
    notes, source_tpb = parse_midi_file(midi_path)
    if not notes:
        print("No notes found in MIDI file")
        sys.exit(1)

    note_names = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
    print(f"Source MIDI: {len(notes)} notes, TPB={source_tpb}")
    pitches = sorted(set(n[1] for n in notes))
    print(f"  Pitch range: {note_names[pitches[0]%12]}{pitches[0]//12-1} to "
          f"{note_names[pitches[-1]%12]}{pitches[-1]//12-1}")
    print(f"  Velocity range: {min(n[2] for n in notes)}-{max(n[2] for n in notes)}")

    # Read ProjectData
    with open(project_data_path, 'rb') as f:
        data = bytearray(f.read())

    print(f"ProjectData: {len(data)} bytes")

    # Find EvSq chunks
    chunks = find_evsq_chunks(data)
    note_chunks = [c for c in chunks if c['is_note_chunk']]

    if note_chunks:
        print(f"Found {len(note_chunks)} note EvSq chunk(s):")
        for i, c in enumerate(note_chunks):
            status = "empty" if c['data_size'] <= 16 else f"{c['num_events']} events"
            print(f"  [{i}] track_id={c['track_id']}, sub=0x{c['sub_id']:08X}, "
                  f"{status}, offset={c['offset']}")

    # Select target chunk
    target = None
    if note_chunks and not new_track:
        if track_id is not None:
            matches = [c for c in note_chunks if c['track_id'] == track_id]
            if matches:
                target = matches[0]
        elif evsq_index is not None:
            if evsq_index < len(note_chunks):
                target = note_chunks[evsq_index]
        else:
            # Default: use the largest note chunk (prefer non-empty)
            non_empty = [c for c in note_chunks if c['num_events'] > 0]
            if non_empty:
                target = max(non_empty, key=lambda c: c['num_events'])
            else:
                target = note_chunks[0]

    base_tick = 38400  # Standard base tick for bar 1

    if target is not None:
        # --- Replace existing note chunk ---
        print(f"\nTarget: track_id={target['track_id']}, "
              f"{target['num_events']} existing events at offset {target['offset']}")

        chunk_off = target['offset']

        # Read base_tick from the sentinel at chunk+40
        stored_base = struct.unpack_from('<I', data, chunk_off + 40)[0]
        if stored_base < 1_000_000:
            base_tick = stored_base
        print(f"Base tick from chunk header: {base_tick}")

        # If this is a pristine empty region, activate its metadata
        if target['data_size'] <= 16:
            target_sub_key = target['sub_id'] >> 16

            # Insert display index entry if missing (may resize data)
            data, display_inserted = ensure_display_index_entry(
                data, target['track_id'], target_sub_key, chunks)
            if display_inserted:
                # Re-find chunks since offsets changed
                chunks = find_evsq_chunks(data)
                note_chunks = [c for c in chunks if c['is_note_chunk']]
                # Re-find our target chunk
                for c in note_chunks:
                    if c['sub_id'] == target['sub_id'] and c['track_id'] == target['track_id']:
                        target = c
                        break
                chunk_off = target['offset']

            activate_region_metadata(data, target['track_id'],
                                     target_sub_key, chunks)

            # Compute region length for display
            # Units: 256 ticks at 960 TPB. 15 units = 1 bar at 4/4.
            # Round up to next 4-bar boundary (60 units).
            scale = 960 / source_tpb
            max_end_tick = max(t + d for t, _, _, d in notes)
            region_ticks_960 = int(max_end_tick * scale)
            region_length_units = region_ticks_960 // 256
            # Round up to next 4-bar boundary (60 units)
            region_length_units = ((region_length_units + 59) // 60) * 60

            activate_qesm_region(data, target['track_id'],
                                 target_sub_key,
                                 region_length_units)

        # Build new event data (sentinel + events + terminator, goes at +32)
        new_event_data = build_evsq_events(notes, base_tick, source_tpb)

        old_data_size = target['data_size']
        new_data_size = len(new_event_data) - 4
        size_delta = new_data_size - old_data_size

        print(f"Old data_size: {old_data_size}, New data_size: {new_data_size}, "
              f"Delta: {size_delta:+d}")

        before = data[:chunk_off + 32]
        old_chunk_end = chunk_off + 36 + old_data_size
        after = data[old_chunk_end:]

        struct.pack_into('<I', before, chunk_off + 28, new_data_size)
    else:
        # --- Create new note chunk with binary manipulation ---
        target_track = track_id if track_id is not None else None

        track_chunks = [c for c in chunks if target_track is None or c['track_id'] == target_track]
        if not track_chunks:
            track_chunks = chunks

        if not track_chunks:
            print("Error: No EvSq chunks found to determine insertion point")
            sys.exit(1)

        if target_track is None:
            from collections import Counter
            track_counts = Counter(c['track_id'] for c in chunks)
            target_track = track_counts.most_common(1)[0][0]
            track_chunks = [c for c in chunks if c['track_id'] == target_track]

        # Find existing sub_ids to pick the next available slot
        all_existing_sids = set(c['sub_id'] for c in track_chunks)
        new_sub_id = 0x001C0000
        while new_sub_id in all_existing_sids:
            new_sub_id += 0x00040000
        new_sub_key = new_sub_id >> 16

        # Pick a chunk ID for the new EvSq
        existing_ids = sorted(struct.unpack_from('<H', data, c['offset'] + 14)[0]
                              for c in track_chunks)
        new_id = max(existing_ids) + 2 if existing_ids else 0x80

        # Generate UUID for the new track
        new_uuid = generate_uuid_bytes()

        print(f"\nCreating new track via binary manipulation.")
        print(f"  Sub key: 0x{new_sub_key:04X} (sub_id=0x{new_sub_id:08X})")
        print(f"  Track ID: {target_track}, Chunk ID: 0x{new_id:04X}")
        print(f"  UUID: {new_uuid.hex()}")

        # --- Region 1: Expand OCuA entry (skips if no Inst stubs available) ---
        ocua_entries = find_ocua_entries(data)
        ocua_result = expand_ocua_entry(data, ocua_entries, new_uuid)
        if ocua_result:
            data, _ocua_idx, ocua_delta = ocua_result
            chunks = find_evsq_chunks(data)
            track_chunks = [c for c in chunks if c['track_id'] == target_track]

        # --- Region 2: Modify track index ---
        track_idx_result = modify_track_index(data, new_uuid, track_name="Inst")
        if track_idx_result:
            data, tidx_delta, track_index_value = track_idx_result
            chunks = find_evsq_chunks(data)
            track_chunks = [c for c in chunks if c['track_id'] == target_track]

        # --- Region 3: Insert karT entry ---
        kart_result = insert_kart_entry(data, target_track, new_uuid,
                                        track_index_value if track_idx_result else 0x58)
        if kart_result:
            data, kart_delta = kart_result
            chunks = find_evsq_chunks(data)
            track_chunks = [c for c in chunks if c['track_id'] == target_track]

        # --- Region 4: Update JSON drummer state ---
        json_result = update_json_drummer_state(data, new_uuid)
        if json_result:
            data, json_delta = json_result

        # Re-find insertion point after all structural changes
        insert_after = None
        for c in sorted(track_chunks, key=lambda c: c['sub_id']):
            if c['sub_id'] < new_sub_id:
                insert_after = c
        if insert_after is None:
            insert_after = track_chunks[-1]
        insert_offset = insert_after['offset'] + 36 + insert_after['data_size']

        print(f"  Insertion point: byte {insert_offset}")

        # Build note event data
        new_event_data = build_evsq_events(notes, base_tick, source_tpb)
        new_data_size = len(new_event_data) - 4

        # --- Build 32-byte EvSq header ---
        evsq_header = bytearray(32)
        evsq_header[0:4] = b'qSvE'
        struct.pack_into('<H', evsq_header, 4, 1)
        struct.pack_into('<H', evsq_header, 6, target_track)
        struct.pack_into('<I', evsq_header, 8, new_sub_id)
        struct.pack_into('<H', evsq_header, 14, new_id)
        evsq_header[18:22] = b'\xFF\xFF\xFF\xFF'
        struct.pack_into('<I', evsq_header, 22, 2)
        struct.pack_into('<H', evsq_header, 26, 2)
        struct.pack_into('<I', evsq_header, 28, new_data_size)

        # --- Build 369-byte qeSM region ---
        QESM_TEMPLATE = (
            b"\x71\x65\x53\x4d\x05\x00\x17\x00\x00\x00\x1c\x00\x00\x00\xff\xff"
            b"\xff\xff\xff\xff\xff\xff\x02\x00\x00\x00\x02\x00\x29\x01\x00\x00"
            b"\x00\x00\x00\x00\x70\x03\x01\x00\x00\x00\x00\x00\x23\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x09"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x78\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x06\x00\x00\x00\x00\x06\x8a\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x58\x00\x00\x00\x00\x00\x00\x00\xff\xff\x00\x00\x04\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00"
            b"\x00\x00\xff\x00\x00\x00\x00\x00\xff\xff\xff\xff\x00\x6b\x61\x72"
            b"\x54\x06\x00\x17\x00\x00\x00\x1c\x00\x00\x00\xff\xff\xff\xff\xff"
            b"\xff\xff\x7f\x02\x00\x00\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00"
        )
        qesm = bytearray(QESM_TEMPLATE)
        struct.pack_into('<H', qesm, 6, target_track)
        struct.pack_into('<H', qesm, 10, new_sub_key)
        struct.pack_into('<H', qesm, 44, new_id)
        struct.pack_into('<H', qesm, 339, target_track)
        struct.pack_into('<H', qesm, 343, new_sub_key)
        max_tick = max(n[0] + n[3] for n in notes)
        region_len_ticks = int(max_tick * (960 / source_tpb))
        region_len_bars = (region_len_ticks + 3839) // 3840
        region_len = region_len_bars * 3840
        struct.pack_into('<H', qesm, 258, region_len)

        # --- Update EvSq 0x00040000 (region metadata) ---
        chunk_04 = None
        for c in track_chunks:
            if c['sub_id'] == 0x00040000:
                chunk_04 = c
                break

        REGION_ENTRY_TEMPLATE = (
            b"\x20\x00\x00\x00\x00\x87\x00\x00\x1c\x00\x00\x00"
            b"\x00\x04\x00\x00\x58\x00\x00\x00\x01\x00\x00\x89\x00\x00\x00\x00"
            b"\xff\xff\xff\x3f\x1c\x00\x00\x00\x00\x00\x00\x88\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x06\x00\x00\x00\x00\x06\x8a\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x88\x00\x00\x00\x00"
        )
        region_entry = bytearray(REGION_ENTRY_TEMPLATE)
        struct.pack_into('<H', region_entry, 8, new_sub_key)
        struct.pack_into('<H', region_entry, 32, new_sub_key)
        struct.pack_into('<H', region_entry, 16, region_len)

        if chunk_04 and chunk_04['data_size'] == 16:
            prefix = b'\x00\x00\x00\x00'
            terminator = (
                b"\xf1\x00\x00\x00\xff\xff\xff\x3f\x00\x00\x00\x00"
                b"\x00\x00\x00\x00"
            )
            region_data = bytearray(prefix + bytes(region_entry) + terminator)
            c04_new_data_size = len(region_data) - 4
            c04_off = chunk_04['offset']
            c04_old_end = c04_off + 36 + 16
            struct.pack_into('<I', data, c04_off + 28, c04_new_data_size)
            data = data[:c04_off + 32] + region_data + data[c04_old_end:]
            print(f"  EvSq 0x04: created region data ({c04_new_data_size} bytes)")
            size_shift = c04_new_data_size - 16
            if insert_offset > c04_off:
                insert_offset += size_shift
        elif chunk_04 and chunk_04['data_size'] >= 96:
            c04_off = chunk_04['offset']
            c04_ds = chunk_04['data_size']
            c04_data_start = c04_off + 32
            c04_data_end = c04_off + 36 + c04_ds
            term_offset = None
            for i in range(c04_data_start + 4, c04_data_end - 4):
                if data[i] == 0xF1 and data[i + 1] == 0x00:
                    term_check = struct.unpack_from('<I', data, i + 4)[0]
                    if term_check == 0x3FFFFFFF:
                        term_offset = i
                        break
            if term_offset:
                new_04_data = (data[c04_data_start:term_offset]
                               + bytes(region_entry)
                               + data[term_offset:c04_data_end])
                new_04_ds = len(new_04_data) - 4
                struct.pack_into('<I', data, c04_off + 28, new_04_ds)
                data = data[:c04_data_start] + new_04_data + data[c04_data_end:]
                size_shift = new_04_ds - c04_ds
                print(f"  EvSq 0x04: appended region entry ({c04_ds} -> {new_04_ds})")
                if insert_offset > c04_off:
                    insert_offset += size_shift

        # --- Splice qeSM + EvSq into file ---
        insert_blob = bytes(qesm) + bytes(evsq_header) + new_event_data
        before = data[:insert_offset]
        after = data[insert_offset:]
        new_event_data = insert_blob
        size_delta = 0

    new_data = before + new_event_data + after

    # Update global file size at offset +16
    new_global_size = len(new_data) - 24
    struct.pack_into('<I', new_data, 16, new_global_size)

    # Backup and write
    backup_path = project_data_path + ".bak"
    if not os.path.exists(backup_path):
        shutil.copy2(project_data_path, backup_path)
        print(f"Backup: {backup_path}")
    else:
        print(f"Backup already exists: {backup_path}")

    with open(project_data_path, 'wb') as f:
        f.write(new_data)

    print(f"Wrote: {project_data_path} ({len(new_data)} bytes, was {len(data)})")

    # Verify by re-reading
    verify_chunks = find_evsq_chunks(bytes(new_data))
    verify_note = [c for c in verify_chunks if c['is_note_chunk']]
    if verify_note:
        vc = verify_note[0]
        print(f"Verify: {vc['num_events']} events, data_size={vc['data_size']} "
              f"at offset {vc['offset']}")
    else:
        print("Warning: Could not verify written chunk")


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.mid> <project.logicx> "
              f"[--track-id N] [--evsq-index N] [--new-track]")
        sys.exit(1)

    midi_path = sys.argv[1]
    logicx_path = sys.argv[2]

    track_id = None
    evsq_index = None
    new_track = False

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == '--track-id' and i + 1 < len(sys.argv):
            track_id = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == '--evsq-index' and i + 1 < len(sys.argv):
            evsq_index = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == '--new-track':
            new_track = True
            i += 1
        else:
            i += 1

    inject_midi(logicx_path, midi_path, evsq_index=evsq_index,
                track_id=track_id, new_track=new_track)


if __name__ == "__main__":
    main()
