#!/usr/bin/env python3
"""Pipeline: Logic Pro → Anticipatory Music Transformer → Logic Pro

Extracts MIDI from a Logic Pro project (or reads a MIDI file directly),
generates a continuation using the AMT model, and optionally injects the
result back into the project.

Usage:
    # From a Logic Pro project
    python amt_pipeline.py <project.logicx> [options]

    # From a MIDI file directly
    python amt_pipeline.py <input.mid> [options]
"""

import sys
import os
import argparse

import mido


LOGIC_PROGRAM_PALETTE = (
    0, 6, 16, 19, 24, 27, 32, 33,
    40, 42, 48, 52, 56, 64, 65, 71,
    72, 73, 80, 81, 88, 89, 96, 97,
    104, 105, 112, 113, 118, 120
)


def _logic_track_identity(track_id=None, sub_id=None, slot=0, notes=None):
    """Assign a stable fallback GM program/channel for Logic-extracted notes.

    Logic project extraction currently does not recover the original patch or
    MIDI channel, so we use stable fallback identities instead of collapsing
    every extracted region to channel 0 / program 0 (piano).  When `notes` look
    like drums (drum_detect), the region goes on channel 9 so AMT reads it as
    instrument 128 rather than as low pitched notes.
    """
    if notes is not None:
        from drum_detect import classify_part
        if classify_part(notes)[0]:
            return 0, 9
    seed = slot
    if track_id is not None:
        seed ^= int(track_id) * 1315423911
    if sub_id is not None:
        seed ^= int(sub_id) * 2654435761

    program = LOGIC_PROGRAM_PALETTE[seed % len(LOGIC_PROGRAM_PALETTE)]
    channel = slot % 15
    if channel >= 9:
        channel += 1  # channel 9 is reserved for drums
    return program, channel


def get_midi_tempo_bpm(mid):
    """Return the first MIDI tempo marker as BPM, falling back to 120."""
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                return float(mido.tempo2bpm(msg.tempo))
    return 120.0


def list_tracks(input_path, is_midi):
    """List all tracks with MIDI notes as a JSON-serializable list.

    Also writes a temporary MIDI file for each track (for preview/playback).
    """
    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="amt_tracks_")

    if is_midi:
        mid = mido.MidiFile(input_path)
        tracks = []
        for i, track in enumerate(mid.tracks):
            notes = sum(1 for m in track
                        if m.type == 'note_on' and m.velocity > 0)
            if notes == 0:
                continue
            # Get track name if present
            name = ""
            program = -1
            for m in track:
                if m.type == 'track_name':
                    name = m.name
                elif m.type == 'program_change':
                    program = m.program

            # Write a temp MIDI for this track
            tmp_mid = mido.MidiFile(type=1, ticks_per_beat=mid.ticks_per_beat)
            # Include track 0 (tempo/meta) if this isn't track 0
            if i > 0 and len(mid.tracks) > 0:
                tmp_mid.tracks.append(mid.tracks[0])
            tmp_mid.tracks.append(track)
            midi_path = os.path.join(tmp_dir, f"track_{i}.mid")
            tmp_mid.save(midi_path)

            tracks.append({
                "index": i,
                "name": name or f"Track {i}",
                "notes": notes,
                "program": program,
                "type": "midi",
                "midi_path": midi_path,
            })
        return tracks
    else:
        import extract_midi
        project_data_path = os.path.join(
            input_path, "Alternatives", "000", "ProjectData")
        with open(project_data_path, 'rb') as f:
            data = f.read()
        meta = extract_midi.read_metadata(input_path)
        regions = extract_midi.list_evsq_regions(data)
        # Deduplicate
        seen = set()
        tracks = []
        track_num = 0
        for r in regions:
            notes_list = extract_midi.extract_midi_from_projectdata(
                data, track_id=r['track_id'], sub_id=r['sub_id'])
            if not notes_list:
                continue
            sig = tuple((t, p, v, d) for t, p, v, d in notes_list)
            if sig in seen:
                continue
            seen.add(sig)
            track_num += 1
            # Compute duration in seconds
            if notes_list:
                max_tick = max(t + d for t, _, _, d in notes_list)
                dur_sec = max_tick / 480.0 * (60.0 / meta['tempo'])
            else:
                dur_sec = 0

            # Write a temp MIDI for this track
            tmp_mid = mido.MidiFile(type=1, ticks_per_beat=480)
            tempo_track = mido.MidiTrack()
            tmp_mid.tracks.append(tempo_track)
            uspb = int(60_000_000 / meta['tempo'])
            tempo_track.append(
                mido.MetaMessage('set_tempo', tempo=uspb, time=0))
            tempo_track.append(
                mido.MetaMessage('end_of_track', time=0))
            note_track = mido.MidiTrack()
            tmp_mid.tracks.append(note_track)
            program, channel = _logic_track_identity(
                r['track_id'], r['sub_id'], track_num - 1, notes_list)
            note_track.append(
                mido.Message('program_change', program=program,
                             channel=channel, time=0))
            events = []
            for tick, pitch, vel, dur in notes_list:
                events.append((tick, 'note_on', pitch, vel, channel))
                events.append((tick + dur, 'note_off', pitch, 0, channel))
            events.sort(key=lambda e: (e[0], e[1] == 'note_on'))
            prev_tick = 0
            for tick, msg_type, pitch, vel, msg_channel in events:
                delta = max(0, tick - prev_tick)
                note_track.append(
                    mido.Message(msg_type, note=pitch, velocity=vel,
                                 channel=msg_channel, time=delta))
                prev_tick = tick
            note_track.append(
                mido.MetaMessage('end_of_track', time=0))
            midi_path = os.path.join(tmp_dir, f"track_{track_num}.mid")
            tmp_mid.save(midi_path)

            tracks.append({
                "track_id": r['track_id'],
                "sub_id": f"0x{r['sub_id']:08X}",
                "notes": len(notes_list),
                "duration": round(dur_sec, 1),
                "name": f"Track {track_num}",
                "is_drum": channel == 9,
                "type": "logicx",
                "midi_path": midi_path,
            })
        return tracks


def filter_midi_track(mid, track_index, debug=False):
    """Return a new MidiFile containing only the specified track (+ track 0 for tempo)."""
    new_mid = mido.MidiFile(type=1, ticks_per_beat=mid.ticks_per_beat)
    # Always include track 0 (tempo/meta)
    if len(mid.tracks) > 0:
        new_mid.tracks.append(mid.tracks[0])
    # Add the selected track (if it's not track 0)
    if track_index > 0 and track_index < len(mid.tracks):
        new_mid.tracks.append(mid.tracks[track_index])
    elif track_index == 0 and len(mid.tracks) > 0:
        pass  # already added
    if debug:
        notes = sum(1 for t in new_mid.tracks for m in t
                    if m.type == 'note_on' and m.velocity > 0)
        print(f"  Filtered to MIDI track {track_index}: {notes} notes")
    return new_mid


def extract_all_midi(logicx_path, track_id=None, sub_id=None, debug=False):
    """Stage 1: Extract all MIDI regions from a Logic Pro project.

    Args:
        track_id: If set, only extract regions matching this track ID.
        sub_id: If set, only extract the region with this exact sub ID.

    Returns:
        all_notes: list of (notes, track_id, sub_id) tuples
        meta: dict with tempo, key, time sig
    """
    import extract_midi

    project_data_path = os.path.join(
        logicx_path, "Alternatives", "000", "ProjectData")
    if not os.path.exists(project_data_path):
        print(f"Error: {project_data_path} not found")
        sys.exit(1)

    with open(project_data_path, 'rb') as f:
        data = f.read()

    meta = extract_midi.read_metadata(logicx_path)
    regions = extract_midi.list_evsq_regions(data)

    if debug:
        print(f"  ProjectData: {len(data)} bytes")
        print(f"  Tempo: {meta['tempo']} BPM, Key: {meta['key']}, "
              f"Time: {meta['time_num']}/{meta['time_den']}")
        print(f"  Found {len(regions)} MIDI regions")

    all_notes = []
    seen_signatures = set()
    for r in regions:
        # Filter by track_id if specified
        if track_id is not None and r['track_id'] != track_id:
            continue
        # Filter by sub_id if specified
        if sub_id is not None and r['sub_id'] != sub_id:
            continue

        notes = extract_midi.extract_midi_from_projectdata(
            data, track_id=r['track_id'], sub_id=r['sub_id'])
        if notes:
            # Deduplicate regions with identical note content
            sig = tuple((t, p, v, d) for t, p, v, d in notes)
            if sig in seen_signatures:
                if debug:
                    print(f"    Track {r['track_id']} sub 0x{r['sub_id']:08X}: "
                          f"{len(notes)} notes (skipped, duplicate)")
                continue
            seen_signatures.add(sig)
            all_notes.append((notes, r['track_id'], r['sub_id']))
            if debug:
                print(f"    Track {r['track_id']} sub 0x{r['sub_id']:08X}: "
                      f"{len(notes)} notes")

    return all_notes, meta


def build_mido_file(all_notes, meta, debug=False):
    """Stage 2a: Build a mido.MidiFile from extracted notes.

    Creates Format 1 MIDI with 480 TPB. MIDI input preserves its original
    program changes; Logic extraction uses stable fallback identities so
    regions do not collapse to a single piano instrument token stream.
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=480)

    # Track 0: tempo
    tempo_track = mido.MidiTrack()
    mid.tracks.append(tempo_track)
    uspb = int(60_000_000 / meta['tempo'])
    tempo_track.append(mido.MetaMessage('set_tempo', tempo=uspb, time=0))
    tempo_track.append(mido.MetaMessage('end_of_track', time=0))

    total_notes = 0
    for slot, (notes, track_id, sub_id) in enumerate(all_notes):
        track = mido.MidiTrack()
        mid.tracks.append(track)
        program, channel = _logic_track_identity(track_id, sub_id, slot, notes)
        track.append(mido.Message('program_change', program=program,
                                  channel=channel, time=0))

        # Build note_on/note_off events sorted by tick
        events = []
        for tick, pitch, vel, dur in notes:
            events.append((tick, 'note_on', pitch, vel, channel))
            events.append((tick + dur, 'note_off', pitch, 0, channel))
        events.sort(key=lambda e: (e[0], e[1] == 'note_on'))

        prev_tick = 0
        for tick, msg_type, pitch, vel, msg_channel in events:
            delta = max(0, tick - prev_tick)
            track.append(mido.Message(msg_type, note=pitch, velocity=vel,
                                      channel=msg_channel, time=delta))
            prev_tick = tick

        track.append(mido.MetaMessage('end_of_track', time=0))
        total_notes += len(notes)

    if debug:
        print(f"  Built MIDI: {len(mid.tracks)} tracks, {total_notes} notes, "
              f"tempo={meta['tempo']} BPM")

    return mid


def load_midi_file(midi_path, debug=False):
    """Load a MIDI file directly, preserving original instruments."""
    mid = mido.MidiFile(midi_path)
    if debug:
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'program_change':
                    print(f"  Instrument: program {msg.program}")
    return mid


def load_model(device, model_size='small', debug=False):
    """Load an AMT model via PyTorch."""
    import torch
    from transformers import AutoModelForCausalLM

    # Hugging Face checkpoints from the AMT paper; set AMT_MODEL_<SIZE> to a
    # local directory to load an offline copy instead.
    hf_paths = {
        'small': 'stanford-crfm/music-small-800k',
        'medium': 'stanford-crfm/music-medium-800k',
        'large': 'stanford-crfm/music-large-800k',
    }
    hf_path = os.environ.get(f"AMT_MODEL_{model_size.upper()}",
                             hf_paths[model_size])
    # These repos ship .bin weights; don't let transformers try to open a
    # safetensors conversion PR on the Hub in a background thread.
    os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "1")
    print(f"Loading AMT model ({model_size})...")
    try:
        model = AutoModelForCausalLM.from_pretrained(
            hf_path,
            attn_implementation='sdpa',
        )
    except TypeError:
        # Older Transformers builds may not accept attn_implementation.
        model = AutoModelForCausalLM.from_pretrained(hf_path)

    # HF's SDPA attention ignores scale_attn_by_inverse_layer_idx, so bake
    # the 1/(layer+1) scaling directly into each layer's Q projection weights.
    # This lets us keep fast SDPA while matching the trained attention scaling.
    if getattr(model.config, 'scale_attn_by_inverse_layer_idx', False):
        with torch.no_grad():
            n_embd = model.config.n_embd
            for layer_idx, block in enumerate(model.transformer.h):
                scale = 1.0 / (layer_idx + 1)
                # c_attn.weight is [n_embd, 3*n_embd]: columns 0:n_embd are Q
                block.attn.c_attn.weight[:, :n_embd] *= scale
                block.attn.c_attn.bias[:n_embd] *= scale
        model.config.scale_attn_by_inverse_layer_idx = False

    model.eval()

    if device == 'mps' and torch.backends.mps.is_available():
        model = model.to('mps')
    elif device == 'cuda' and torch.cuda.is_available():
        model = model.to('cuda')
    else:
        if device != 'cpu':
            print(f"  Warning: {device} not available, falling back to CPU")
        model = model.to('cpu')

    if debug:
        print(f"  Model loaded on {model.device}")
    return model



def _compute_content_end(events):
    """Compute content end as max(time + duration) from AMT tokens."""
    from anticipation.config import TIME_RESOLUTION
    from anticipation.vocab import TIME_OFFSET, DUR_OFFSET, \
        SEPARATOR, SPECIAL_OFFSET, CONTROL_OFFSET, \
        ATIME_OFFSET, ADUR_OFFSET

    content_end = 0
    for time_tok, dur_tok, note_tok in zip(
            events[0::3], events[1::3], events[2::3]):
        if note_tok == SEPARATOR or note_tok >= SPECIAL_OFFSET:
            continue
        if note_tok < CONTROL_OFFSET:
            t = (time_tok - TIME_OFFSET) / TIME_RESOLUTION
            d = (dur_tok - DUR_OFFSET) / TIME_RESOLUTION
        else:
            t = (time_tok - ATIME_OFFSET) / TIME_RESOLUTION
            d = (dur_tok - ADUR_OFFSET) / TIME_RESOLUTION
        content_end = max(content_end, t + d)
    return content_end


def _prepare_prompt_events(events, num_events, prompt_seconds=None, debug=False):
    """Use as much recent prompt context as fits within the AMT token budget."""
    from anticipation import ops
    from anticipation.config import CONTEXT_SIZE

    original_end_sec = ops.max_time(events, seconds=True) if events else 0.0
    prompt = list(events)

    if prompt_seconds is not None and prompt_seconds > 0:
        clip_start = max(0.0, original_end_sec - float(prompt_seconds))
        prompt = ops.clip(
            prompt,
            clip_start,
            original_end_sec,
            clip_duration=False,
            seconds=True,
        )

    max_prompt = CONTEXT_SIZE - 1 - max(int(num_events), 0) * 3
    max_prompt = (max(max_prompt, 0) // 3) * 3
    prompt_len = min(510, max_prompt, (len(prompt) // 3) * 3)

    if prompt_len <= 0:
        prompt = []
    elif len(prompt) > prompt_len:
        prompt = prompt[-prompt_len:]

    prompt_offset_sec = ops.min_time(prompt, seconds=True) if prompt else original_end_sec
    if prompt:
        prompt = ops.translate(prompt, -prompt_offset_sec, seconds=True)
        effective_prompt_end = ops.max_time(prompt, seconds=True)
    else:
        effective_prompt_end = 0.0

    if prompt_seconds is None or prompt_seconds <= 0:
        print(
            f"  Prompt: using maximum recent context that fits the model budget "
            f"({prompt_len // 3} notes, no seconds clip)"
        )
    else:
        print(
            f"  Prompt: clipped to the last {float(prompt_seconds):.0f}s, "
            f"then fit to the model budget ({prompt_len // 3} notes)"
        )

    if debug:
        print(
            f"  Prompt budget: {prompt_len} tokens "
            f"({prompt_len // 3} notes), offset {prompt_offset_sec:.2f}s, "
            f"span {effective_prompt_end:.2f}s"
        )

    return prompt, original_end_sec, effective_prompt_end


def _events_to_note_rows(events):
    """Decode AMT event triplets into sorted note rows for evaluation/export."""
    from anticipation.config import TIME_RESOLUTION
    from anticipation.vocab import (
        TIME_OFFSET, DUR_OFFSET, NOTE_OFFSET, SPECIAL_OFFSET,
        CONTROL_OFFSET, REST,
    )

    rows = []
    for i in range(0, len(events) - 2, 3):
        t_tok = events[i]
        d_tok = events[i + 1]
        n_tok = events[i + 2]

        if n_tok == REST or n_tok >= SPECIAL_OFFSET or n_tok >= CONTROL_OFFSET:
            continue

        t_bins = t_tok - TIME_OFFSET
        d_bins = d_tok - DUR_OFFSET
        n_raw = n_tok - NOTE_OFFSET
        if t_bins < 0 or d_bins < 0 or n_raw < 0:
            continue

        instr = n_raw // 128
        pitch = max(0, min(127, n_raw % 128))
        rows.append({
            "time_sec": t_bins / TIME_RESOLUTION,
            "dur_sec": max(d_bins / TIME_RESOLUTION, 0.001),
            "pitch": pitch,
            "instr": instr,
        })

    rows.sort(key=lambda r: (r["time_sec"], r["pitch"], r["instr"]))
    return rows


def _assign_row_velocities(rows):
    """Add deterministic, mildly expressive velocities for export/eval."""
    last_pitch_by_instr = {}
    last_time_by_instr = {}
    for idx, row in enumerate(rows):
        instr = row["instr"]
        prev_pitch = last_pitch_by_instr.get(instr, row["pitch"])
        prev_time = last_time_by_instr.get(instr, row["time_sec"])

        dur_boost = min(16, int(round(row["dur_sec"] * 18)))
        leap_boost = min(10, abs(row["pitch"] - prev_pitch) // 2)
        phrase_boost = 6 if idx % 4 == 0 else 0
        spacing_boost = 4 if row["time_sec"] - prev_time > 0.2 else 0

        velocity = 62 + dur_boost + leap_boost + phrase_boost + spacing_boost
        row["velocity"] = max(46, min(100, velocity))

        last_pitch_by_instr[instr] = row["pitch"]
        last_time_by_instr[instr] = row["time_sec"]
    return rows


def _estimate_generation_window_sec(prompt_events, effective_prompt_end, num_events):
    """Choose a single raw generation window from recent prompt density."""
    note_count = max(len(prompt_events) // 3, 1)
    span_sec = max(float(effective_prompt_end), 1.0)
    notes_per_sec = max(note_count / span_sec, 0.5)
    target_span_sec = float(num_events) / notes_per_sec
    return min(max(target_span_sec, 4.0), 60.0)


def generate_events(model, events, num_events=100, top_p=0.95,
                    temperature=1.0, debug=False, seed=None,
                    prompt_seconds=None, piano_only=False):
    """Generate a raw single-pass continuation with no repair or rescoring."""
    from anticipation import ops, sample
    from anticipation.config import TIME_RESOLUTION, MAX_INSTR, MAX_PITCH
    from anticipation.vocab import NOTE_OFFSET
    import numpy as np
    import random
    import torch

    prompt_events, prompt_end_sec, effective_prompt_end = _prepare_prompt_events(
        events,
        num_events=num_events,
        prompt_seconds=prompt_seconds,
        debug=debug,
    )
    if seed is None:
        seed = random.SystemRandom().randrange(0, 2**31)
        if debug:
            print(f"  Seed: {seed} (random)")
    else:
        seed = int(seed)
        print(f"  Seed: {seed}")
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)

    window_sec = _estimate_generation_window_sec(
        prompt_events, effective_prompt_end, num_events)
    end_time_sec = effective_prompt_end + window_sec

    print(f"  Input: {prompt_end_sec:.1f}s, generating {num_events} notes...")
    print(f"  Raw generation window: {window_sec:.1f}s")
    print(f"  Temperature: {temperature:.2f}")
    if piano_only:
        print("  Instrument constraint: piano only")

    original_instr_logits = sample.instr_logits
    if piano_only:
        def _piano_only_logits(logits, full_history):
            logits = original_instr_logits(logits, full_history)
            for instr in range(1, MAX_INSTR):
                logits[
                    NOTE_OFFSET + instr * MAX_PITCH:
                    NOTE_OFFSET + (instr + 1) * MAX_PITCH
                ] = -float("inf")
            return logits
        sample.instr_logits = _piano_only_logits

    from amt_compat import sampling_temperature
    try:
        with sampling_temperature(temperature):
            output = sample.generate_ar(
                model,
                start_time=effective_prompt_end,
                end_time=end_time_sec,
                inputs=prompt_events,
                top_p=top_p,
                debug=debug,
            )
    finally:
        sample.instr_logits = original_instr_logits

    clip_start_sec = effective_prompt_end + (1.0 / TIME_RESOLUTION)
    generated = ops.unpad(
        ops.clip(
            output,
            clip_start_sec,
            end_time_sec,
            clip_duration=False,
            seconds=True,
        )
    )

    # Truncate to requested number of notes
    if len(generated) // 3 > num_events:
        generated = generated[:num_events * 3]

    real_notes = len(generated) // 3
    if real_notes > 0:
        gen_span = ops.max_time(generated, seconds=True) - ops.min_time(generated, seconds=True)
        print(f"  Generated {real_notes} notes ({gen_span:.1f}s)")
    else:
        print(f"  Generated 0 notes")

    return generated, prompt_end_sec



def generated_to_midi(generated_events, prompt_end_sec, output_path,
                      tempo_bpm=120.0, debug=False, start_at_zero=True):
    """Convert generated AMT tokens to a MIDI file.

    The generated_events have absolute time tokens relative to the
    generated continuation. For standalone output, we write them
    starting at time 0. For Logic injection workflows, we can keep the
    prompt offset so the continuation lands after the input phrase.
    """
    from anticipation.config import TIME_RESOLUTION
    from anticipation.vocab import (TIME_OFFSET, DUR_OFFSET, NOTE_OFFSET,
                                     MAX_TIME, MAX_DUR, SPECIAL_OFFSET,
                                     CONTROL_OFFSET, REST)

    if not generated_events:
        print("  Warning: no new events generated")
        return None

    # Validate and filter tokens
    valid_events = []
    skipped_invalid = 0
    skipped_rest = 0
    for i in range(0, len(generated_events) - 2, 3):
        t_tok = generated_events[i]
        d_tok = generated_events[i + 1]
        n_tok = generated_events[i + 2]

        if n_tok == REST:
            skipped_rest += 1
            continue

        t = t_tok - TIME_OFFSET
        d = d_tok - DUR_OFFSET
        n = n_tok - NOTE_OFFSET

        if not (0 <= t and 0 <= d < MAX_DUR and
                0 <= n and n_tok < CONTROL_OFFSET):
            skipped_invalid += 1
            if debug:
                print(f"    Skipping invalid event: t={t} d={d} n={n}")
            continue
        valid_events.extend([t_tok, d_tok, n_tok])

    if skipped_invalid > 0:
        print(f"  Filtered: {skipped_invalid} invalid")
    if skipped_rest > 0:
        print(f"  Dropped: {skipped_rest} REST events")

    if not valid_events:
        print("  Warning: all events were invalid")
        return None

    num_notes = len(valid_events) // 3
    min_t_sec = _compute_min_time(valid_events)
    max_t_sec = max((valid_events[i] - TIME_OFFSET) / TIME_RESOLUTION
                    for i in range(0, len(valid_events), 3))
    gen_span = max_t_sec - min_t_sec
    placement_sec = 0.0 if start_at_zero else prompt_end_sec
    print(f"  Generated {num_notes} notes, "
          f"span {gen_span:.1f}s, placed at {placement_sec:.1f}s")

    # Convert directly to MIDI, preserving instruments via channels
    mid = mido.MidiFile(type=1, ticks_per_beat=480)

    # Preserve the source tempo instead of forcing 120 BPM.
    uspb = int(round(60_000_000 / max(tempo_bpm, 1e-6)))
    ticks_per_sec = 480 * (1000000 / uspb)  # 960

    rows = _assign_row_velocities(_events_to_note_rows(valid_events))

    # Group notes by GM instrument
    from collections import defaultdict
    instr_notes = defaultdict(list)
    for row in rows:
        instr = row["instr"]
        pitch = row["pitch"]
        time_sec = row["time_sec"] - min_t_sec + placement_sec
        dur_sec = row["dur_sec"]
        vel = row["velocity"]
        on_tick = max(0, int(round(time_sec * ticks_per_sec)))
        off_tick = on_tick + max(1, int(round(dur_sec * ticks_per_sec)))
        instr_notes[instr].append((on_tick, off_tick, pitch, vel))

    if debug:
        print(f"  Instruments: {sorted(instr_notes.keys())}")

    # Track 0: tempo
    tempo_track = mido.MidiTrack()
    mid.tracks.append(tempo_track)
    tempo_track.append(mido.MetaMessage('set_tempo', tempo=uspb, time=0))
    tempo_track.append(mido.MetaMessage('end_of_track', time=0))

    # One track per instrument (use channels 0-15, drums on ch 9)
    ch = 0
    for instr in sorted(instr_notes.keys()):
        notes = instr_notes[instr]
        track = mido.MidiTrack()
        mid.tracks.append(track)

        midi_ch = 9 if instr == 128 else ch  # drums = instrument 128
        gm_program = 0 if instr == 128 else min(instr, 127)
        if midi_ch != 9:
            ch = (ch + 1) if ch < 8 else (ch + 2)  # skip ch 9
            if ch > 15:
                ch = 0

        track.append(mido.Message('program_change', program=gm_program,
                                   channel=midi_ch, time=0))

        midi_events = []
        for on_tick, off_tick, pitch, vel in notes:
            midi_events.append((on_tick, 'note_on', pitch, vel))
            midi_events.append((off_tick, 'note_off', pitch, 0))
        midi_events.sort(key=lambda e: (e[0], e[1] == 'note_on'))

        prev_tick = 0
        for tick, msg_type, pitch, vel in midi_events:
            delta = max(0, tick - prev_tick)
            track.append(mido.Message(msg_type, note=pitch, velocity=vel,
                                       channel=midi_ch, time=delta))
            prev_tick = tick
        track.append(mido.MetaMessage('end_of_track', time=0))

    mid.save(output_path)
    if debug:
        print(f"  Wrote: {output_path} ({os.path.getsize(output_path)} bytes)")

    return output_path


def _compute_min_time(events):
    """Get minimum time from AMT tokens in seconds."""
    from anticipation.config import TIME_RESOLUTION
    from anticipation.vocab import TIME_OFFSET, SPECIAL_OFFSET, CONTROL_OFFSET, REST
    min_t = float('inf')
    for i in range(0, len(events), 3):
        if i + 2 >= len(events):
            break
        note_tok = events[i + 2]
        if note_tok == REST:
            continue
        if note_tok >= SPECIAL_OFFSET:
            continue
        if note_tok < CONTROL_OFFSET:
            t = (events[i] - TIME_OFFSET) / TIME_RESOLUTION
        else:
            continue
        min_t = min(min_t, t)
    return min_t if min_t != float('inf') else 0


def main():
    parser = argparse.ArgumentParser(
        description="AMT Pipeline: Logic Pro / MIDI → generate → MIDI / Logic Pro")
    parser.add_argument("input", help="Path to .logicx project or .mid file")
    parser.add_argument("--num-events", type=int, default=100,
                        help="Number of notes to generate (default: 100)")
    parser.add_argument("--top-p", type=float, default=0.98,
                        help="Nucleus sampling threshold (default: 0.98)")
    parser.add_argument("--temperature", type=float, default=1.20,
                        help="Sampling temperature (default: 1.20)")
    parser.add_argument("--piano-only", action="store_true",
                        help="Restrict generated notes to acoustic grand piano (instrument 0)")
    parser.add_argument("--keep-prompt-offset", action="store_true",
                        help="Write output at the original prompt end instead of starting at time 0")
    parser.add_argument("--prompt-seconds", type=float, default=None,
                        help="Use only the last N seconds of input as prompt "
                             "(default: use full input up to token budget)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output MIDI path (default: <input>_generated.mid)")
    parser.add_argument("--list-tracks", action="store_true",
                        help="List tracks as JSON and exit")
    parser.add_argument("--track-id", type=int, default=None,
                        help="Logic track ID to use (from --list-tracks)")
    parser.add_argument("--sub-id", type=str, default=None,
                        help="Logic sub ID hex to use (from --list-tracks)")
    parser.add_argument("--midi-track", type=int, default=None,
                        help="MIDI track index to use (from --list-tracks)")
    parser.add_argument("--no-inject", action="store_true",
                        help="Generate MIDI only, don't inject back")
    parser.add_argument("--evsq-index", type=int, default=None,
                        help="Target EvSq slot for injection (default: auto)")
    parser.add_argument("--device", choices=['mps', 'cpu', 'cuda'],
                        default='mps', help="Compute device (default: mps)")
    parser.add_argument("--model-size",
                        choices=['small', 'medium', 'large'],
                        default='medium',
                        help="AMT model size (default: medium)")
    parser.add_argument("--debug", action="store_true",
                        help="Verbose output")
    parser.add_argument("--seed", type=int, default=None,
                        help="Optional random seed for reproducible generation")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    is_midi = input_path.lower().endswith(('.mid', '.midi'))
    is_logicx = input_path.lower().endswith('.logicx') or os.path.isdir(input_path)

    if not is_midi and not is_logicx:
        print(f"Error: {input_path} is not a .mid or .logicx file")
        sys.exit(1)

    # --list-tracks mode: output JSON and exit
    if args.list_tracks:
        import json
        tracks = list_tracks(input_path, is_midi)
        print(json.dumps(tracks))
        sys.exit(0)

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = args.output or os.path.join(
        os.path.dirname(input_path), base_name + "_generated.mid")

    # Stage 1-2: Get MIDI events
    if is_midi:
        print(f"Loading MIDI file: {input_path}")
        mid = load_midi_file(input_path, debug=args.debug)
        # Filter to a specific track if requested
        if args.midi_track is not None:
            mid = filter_midi_track(mid, args.midi_track, debug=args.debug)
        total_notes = sum(
            1 for track in mid.tracks for m in track
            if m.type == 'note_on' and m.velocity > 0)
        print(f"  {total_notes} notes, {mid.length:.1f}s")
        source_tempo_bpm = get_midi_tempo_bpm(mid)
    else:
        print("Stage 1: Extracting MIDI from Logic Pro project...")
        import extract_midi
        # Parse sub_id from hex string if provided
        sub_id = int(args.sub_id, 16) if args.sub_id else None
        all_notes, meta = extract_all_midi(
            input_path, track_id=args.track_id, sub_id=sub_id,
            debug=args.debug)
        if not all_notes:
            print("Error: no MIDI notes found in project")
            sys.exit(1)
        total = sum(len(n) for n, _, _ in all_notes)
        print(f"  Extracted {total} notes from {len(all_notes)} regions")
        source_tempo_bpm = float(meta['tempo'])

        print("Stage 2: Converting to AMT token format...")
        mid = build_mido_file(all_notes, meta, debug=args.debug)

    # === AMT pipeline: MIDI → AMT tokens → generate → MIDI ===
    from anticipation.convert import midi_to_events
    events = midi_to_events(mid, debug=args.debug)
    print(f"  {len(events)//3} AMT events")

    # Stage 3: Generate continuation
    print(f"Stage 3: Generating {args.num_events} notes with AMT...")
    model = load_model(args.device, model_size=args.model_size,
                       debug=args.debug)
    generated_events, prompt_end = generate_events(
        model, events, num_events=args.num_events,
        top_p=args.top_p,
        temperature=args.temperature,
        debug=args.debug,
        seed=args.seed,
        prompt_seconds=args.prompt_seconds,
        piano_only=args.piano_only)

    # Stage 4: Convert back to MIDI
    print("Stage 4: Converting generated tokens to MIDI...")
    keep_prompt_offset = args.keep_prompt_offset or (is_logicx and not args.no_inject)

    result_path = generated_to_midi(
        generated_events, prompt_end, output_path,
        tempo_bpm=source_tempo_bpm, debug=args.debug,
        start_at_zero=not keep_prompt_offset)
    if not result_path:
        print("Error: generation produced no output")
        sys.exit(1)
    print(f"  Saved: {result_path}")

    # Stage 5: Inject back into Logic Pro (only for .logicx input)
    if is_logicx and not args.no_inject:
        import inject_midi
        print("Stage 5: Injecting into Logic Pro project...")
        inject_midi.inject_midi(
            input_path, result_path,
            evsq_index=args.evsq_index,
            new_track=True)
        print("  Done! Open/reload the project in Logic Pro.")
    elif is_midi:
        print("(MIDI input — skipping Logic Pro injection)")

    print(f"\nPipeline complete. Output: {result_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        # Print to stdout so the Swift app can capture it
        print(f"\nERROR: {e}")
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)
