"""Anticipatory (accompaniment) generation with the Anticipatory Music Transformer.

The existing pipeline calls sample.generate_ar() with [AUTOREGRESS] and no
controls, which is plain left-to-right continuation. This module uses the
anticipatory path instead: sample.generate(inputs=..., controls=...), which
prepends [ANTICIPATE] and interleaves the control events at stopping times
DELTA seconds before their onset. That is what lets the model write a part
that fits around material that already exists in the session.

Note on logit masking: safe_logits() in the anticipation library masks
[CONTROL_OFFSET:SPECIAL_OFFSET] exactly like the AR path does. That is correct
and is not the difference. Controls are input-only -- the model is conditioned
on them but must never emit them. The difference is generate() vs generate_ar()
and passing controls at all.

Each source track is assigned a distinct General MIDI program so that
extract_instruments() can split the token stream into (target, controls).

Usage:
    python anticipatory.py session.logicx --target-track 2
    python anticipatory.py multi.mid --target-instr 33
"""
import os
import sys

import mido

TPB = 480


def build_multitrack_midi(tracks, tempo, tpb=TPB):
    """Assemble per-track note lists into one multi-track MIDI on a shared clock.

    tracks: list of dicts with keys 'instr' (GM program 0-127, or 128 for
            drums, which AMT reads from MIDI channel 9) and 'notes',
            where notes is a list of (tick, pitch, velocity, duration) at `tpb`
            ticks per beat, on a common origin.
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=tpb)

    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage(
        'set_tempo', tempo=int(60_000_000 / tempo), time=0))
    meta.append(mido.MetaMessage('end_of_track', time=0))
    mid.tracks.append(meta)

    for i, t in enumerate(tracks):
        tr = mido.MidiTrack()
        if t['instr'] == 128:
            channel, program = 9, 0
        else:
            channel = i if i < 9 else i + 1      # skip channel 9 (drums)
            program = t['instr']
        tr.append(mido.Message(
            'program_change', program=program, channel=channel, time=0))

        events = []
        for tick, pitch, vel, dur in t['notes']:
            events.append((tick, 'note_on', pitch, vel))
            events.append((tick + dur, 'note_off', pitch, 0))
        events.sort(key=lambda e: (e[0], e[1] == 'note_on'))

        prev = 0
        for tick, kind, pitch, vel in events:
            tr.append(mido.Message(
                kind, note=pitch, velocity=vel,
                channel=channel, time=tick - prev))
            prev = tick
        tr.append(mido.MetaMessage('end_of_track', time=0))
        mid.tracks.append(tr)

    return mid


def split_events(mid, target_instr, debug=False):
    """Tokenize a multi-track MIDI and split it into (target events, controls).

    Everything that is not `target_instr` becomes an anticipated control,
    tagged into the CONTROL_OFFSET block by extract_instruments().
    """
    from anticipation.convert import midi_to_events
    from anticipation.tokenize import extract_instruments

    all_events = midi_to_events(mid)

    present = set()
    from anticipation.vocab import NOTE_OFFSET
    for note_tok in all_events[2::3]:
        present.add((note_tok - NOTE_OFFSET) // 2 ** 7)

    context = sorted(present - {target_instr})
    if debug:
        print(f"  instruments present: {sorted(present)}")
        print(f"  target: {target_instr}   controls: {context}")

    # extract_instruments pulls the *listed* instruments out as controls
    events, controls = extract_instruments(all_events, context)
    return events, controls, context


def generate_accompaniment(model, events, controls, start_time, end_time,
                           top_p=0.98, temperature=1.0, debug=False):
    """Generate a part conditioned on `controls` via the anticipatory path."""
    from anticipation import ops
    from anticipation.sample import generate

    history = ops.clip(events, 0, start_time, clip_duration=False) \
        if events else []

    from amt_compat import sampling_temperature
    with sampling_temperature(temperature):
        generated = generate(
            model, start_time, end_time,
            inputs=history, controls=controls,
            top_p=top_p, debug=debug,
        )
    return generated


def load_logicx_tracks(logicx_path, base_instr=0, debug=False):
    """Read every note-bearing region out of a .logicx, grouped by track.

    Dedup is by (track_id, sub_id) identity, not by note content -- repeated
    loops are real arrangement material and must not collapse into one.
    """
    import extract_midi

    pd = os.path.join(logicx_path, "Alternatives", "000", "ProjectData")
    with open(pd, 'rb') as f:
        data = f.read()
    meta = extract_midi.read_metadata(logicx_path)

    seen = set()
    out = []
    for r in extract_midi.list_evsq_regions(data):
        key = (r['track_id'], r['sub_id'])
        if key in seen:
            continue
        seen.add(key)
        notes = extract_midi.extract_midi_from_projectdata(
            data, track_id=r['track_id'], sub_id=r['sub_id'])
        if not notes:
            continue
        out.append({
            'track_id': r['track_id'],
            'sub_id': r['sub_id'],
            'notes': notes,
        })

    if debug:
        print(f"  {len(out)} note-bearing regions "
              f"(identity dedup, not content dedup)")
    return out, meta['tempo']


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Anticipatory accompaniment generation with AMT")
    parser.add_argument("input", help="Path to a .logicx project or .mid file")
    parser.add_argument("--target-instr", type=int, default=0,
                        help="GM program of the part to generate (default 0)")
    parser.add_argument("--max-regions", type=int, default=4,
                        help="Cap on .logicx regions used as context")
    parser.add_argument("--model-size", default="small",
                        choices=["small", "medium", "large"])
    parser.add_argument("--device", default="mps",
                        choices=["mps", "cpu", "cuda"])
    parser.add_argument("--top-p", type=float, default=0.98)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", default="accompaniment.mid")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.seed is not None:
        import random
        import numpy as np
        import torch
        torch.manual_seed(args.seed)
        np.random.seed(args.seed)
        random.seed(args.seed)

    print(f"Loading {args.input}")
    if args.input.endswith(".logicx"):
        regions, tempo = load_logicx_tracks(args.input, debug=True)
        regions = regions[:args.max_regions]
        # One distinct GM program per pitched region so they can be split
        # apart; detected drum regions share instrument 128, as in AMT.
        # NOTE: regions are extracted on a per-region origin -- absolute
        # arrangement position is not yet recovered from ProjectData.
        from drum_detect import classify_part
        tracks = []
        for i, r in enumerate(regions):
            is_drum, why = classify_part(r['notes'])
            if is_drum:
                instr = 128
            else:
                instr = args.target_instr if i == 0 else 24 + i
            print(f"  region {i}: instr {instr} ({why})")
            tracks.append({'instr': instr, 'notes': r['notes']})
        target = tracks[0]['instr']
        if target == 128 and sum(t['instr'] == 128 for t in tracks) > 1:
            print("  WARNING: target is drums and other drum regions share "
                  "instrument 128; they cannot be used as controls.")
        args.target_instr = target
        mid = build_multitrack_midi(tracks, tempo)
        print(f"  {len(tracks)} parts, tempo {tempo:.1f}")
    else:
        mid = mido.MidiFile(args.input)
        tempo = 120.0

    events, controls, context = split_events(mid, args.target_instr, debug=True)
    if not controls:
        print("  ERROR: no control instruments found -- nothing to anticipate.")
        print("  This would fall back to plain continuation.")
        return 1

    from anticipation import ops
    from anticipation.convert import events_to_midi

    control_end = ops.max_time(controls, seconds=True)
    print(f"  {len(controls)//3} control events spanning {control_end:.1f}s")
    print(f"  {len(events)//3} target events")

    print(f"Loading AMT model ({args.model_size})...")
    from amt_pipeline import load_model
    model = load_model(args.device, model_size=args.model_size)

    print(f"Generating accompaniment over 0-{control_end:.1f}s "
          f"(ANTICIPATE mode, {len(context)} control instruments)...")
    generated = generate_accompaniment(
        model, events, controls, 0, control_end,
        top_p=args.top_p, temperature=args.temperature, debug=args.debug)

    n_gen = len(generated) // 3
    print(f"  generated {n_gen} events")

    combined = ops.combine(generated, controls)
    events_to_midi(combined).save(args.output)
    print(f"  Saved (accompaniment + context): {args.output}")

    events_to_midi(generated).save(
        args.output.replace(".mid", "_solo.mid"))
    print(f"  Saved (accompaniment only): "
          f"{args.output.replace('.mid', '_solo.mid')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
