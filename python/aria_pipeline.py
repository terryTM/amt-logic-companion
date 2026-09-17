#!/usr/bin/env python3
"""Pipeline: MIDI → Aria Music Transformer → MIDI

Uses the Aria Python API with MLX backend directly (no subprocess).

Usage:
    python aria_pipeline.py <input.mid> [options]
"""

import sys
import os
import argparse
import tempfile

import mido
import mlx.core as mx

from ariautils.midi import MidiDict
from ariautils.tokenizer import AbsTokenizer
from aria.inference import get_inference_prompt
from aria.inference.model_mlx import TransformerLM
from aria.inference.sample_mlx import sample_batch
from aria.model import ModelConfig
from aria.config import load_model_config
import aria.inference.sample_mlx as _sample_mlx


# aria's MLX sampler (through at least c2f67bc) calls the model without
# max_kv_pos, which model_mlx requires, so prefill/decode fail with a TypeError.
# Replace both with versions that bound the KV window at the last position.
def _prefill(model, idxs, input_pos, pad_idxs=None):
    return model(idxs=idxs, input_pos=input_pos, offset=input_pos[0],
                 max_kv_pos=input_pos[-1].item(), pad_idxs=pad_idxs)


def _decode_one(model, idxs, input_pos, pad_idxs=None):
    assert input_pos.shape[-1] == 1
    return model(idxs=idxs, input_pos=input_pos, offset=input_pos[0],
                 max_kv_pos=input_pos[-1].item(), pad_idxs=pad_idxs)[:, -1]


_sample_mlx.prefill = _prefill
_sample_mlx.decode_one = _decode_one


ARIA_REPO = 'loubb/aria-medium-base'
ARIA_REVISION = '6a5e00389d3a09d74fde59b18f7acbe6c2ea737a'


def _aria_checkpoint():
    """Local path from ARIA_CHECKPOINT, else the pinned Hugging Face file."""
    path = os.environ.get('ARIA_CHECKPOINT')
    if path:
        return path
    from huggingface_hub import hf_hub_download
    return hf_hub_download(ARIA_REPO, 'model-gen.safetensors',
                           revision=ARIA_REVISION)


def _load_model():
    print("Loading Aria model (MLX)...")
    tokenizer = AbsTokenizer()
    model_config = ModelConfig(**load_model_config(name="medium"))
    model_config.set_vocab_size(tokenizer.vocab_size)
    model = TransformerLM(model_config)
    model.load_weights(_aria_checkpoint(), strict=False)
    mx.eval(model.parameters())
    return model, tokenizer


def _is_note_tok(tok):
    """Non-drum instrument note: ('piano', pitch, velocity)."""
    return (isinstance(tok, tuple) and len(tok) == 3
            and isinstance(tok[0], str)
            and tok[0] not in ("prefix", "onset", "dur"))


def _is_onset_tok(tok):
    return isinstance(tok, tuple) and len(tok) == 2 and tok[0] == "onset"


def _is_dur_tok(tok):
    return isinstance(tok, tuple) and len(tok) == 2 and tok[0] == "dur"


def _is_prefix_tok(tok):
    return isinstance(tok, tuple) and len(tok) >= 2 and tok[0] == "prefix"


def _repair_tokens(seq):
    """Fix missing dur tokens in generated sequences.

    The AbsTokenizer expects strict 3-token triplets for each note:
        (instrument, pitch, velocity) -> ("onset", ms) -> ("dur", ms)
    The model frequently omits the dur token, causing the detokenizer
    to discard the note entirely.  This function inserts a default dur
    wherever one is missing, and removes stray prefix tokens.
    """
    result = []
    seen_notes = False
    i = 0
    while i < len(seq):
        tok = seq[i]

        if _is_note_tok(tok):
            seen_notes = True
            if i + 1 < len(seq) and _is_onset_tok(seq[i + 1]):
                onset = seq[i + 1]
                if i + 2 < len(seq) and _is_dur_tok(seq[i + 2]):
                    # Correct triplet: note, onset, dur
                    result.extend([tok, onset, seq[i + 2]])
                    i += 3
                else:
                    # Missing dur → insert default 500 ms
                    result.extend([tok, onset, ("dur", 500)])
                    i += 2
            else:
                # No onset after note → skip (can't assign timing)
                i += 1

        elif _is_prefix_tok(tok) and seen_notes:
            # Stray prefix mid-sequence → skip
            i += 1

        else:
            result.append(tok)
            i += 1

    return result


def _compress_gaps(midi, max_gap_sec=0.3):
    """Compress large inter-onset gaps while preserving note durations.

    Aria's <T> tokens create 5-second gaps between segments.  AMT packs
    notes densely (12-30 notes/sec).  This function caps the gap between
    consecutive note-on events at max_gap_sec, shifting everything that
    follows by the amount trimmed.
    """
    import bisect

    tpb = midi.ticks_per_beat
    tempo = 500000
    for track in midi.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = msg.tempo
                break
        else:
            continue
        break

    max_gap_ticks = int(mido.second2tick(max_gap_sec, tpb, tempo))

    # Collect all note-on absolute ticks across all tracks
    all_note_ticks = set()
    for track in midi.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                all_note_ticks.add(abs_tick)

    if len(all_note_ticks) < 2:
        return

    sorted_ticks = sorted(all_note_ticks)

    # Build cumulative shift at each note-on boundary
    shift_ticks = [sorted_ticks[0]]
    shift_values = [0]
    total_shift = 0
    for i in range(1, len(sorted_ticks)):
        gap = sorted_ticks[i] - sorted_ticks[i - 1]
        if gap > max_gap_ticks:
            total_shift += gap - max_gap_ticks
        shift_ticks.append(sorted_ticks[i])
        shift_values.append(total_shift)

    if total_shift == 0:
        return

    # Apply shifts to every event in every track
    for track in midi.tracks:
        abs_tick = 0
        events = []
        for msg in track:
            abs_tick += msg.time
            events.append((abs_tick, msg))

        prev_new = 0
        for abs_tick, msg in events:
            idx = bisect.bisect_right(shift_ticks, abs_tick) - 1
            shift = shift_values[idx] if idx >= 0 else 0
            new_tick = max(0, abs_tick - shift)
            msg.time = max(0, new_tick - prev_new)
            prev_new = new_tick


def generate(input_midi, output_path, length=2048, prompt_duration=10,
             temp=0.98, min_p=0.035, top_p=None, variations=1,
             max_duration=None, debug=False):
    """Generate a continuation of a MIDI file using Aria (MLX).

    Args:
        input_midi: path to input MIDI file
        output_path: where to save the result
        length: number of tokens to generate
        prompt_duration: seconds of input to use as prompt
        temp: sampling temperature
        min_p: min-p sampling threshold
        top_p: top-p sampling threshold (None = use min_p instead)
        variations: number of variations to generate
        max_duration: max output duration in seconds (compresses time if needed)
        debug: verbose output
    """
    model, tokenizer = _load_model()

    prompt_ms = int(prompt_duration * 1000)
    midi_dict = MidiDict.from_midi(input_midi)
    prompt = get_inference_prompt(
        midi_dict=midi_dict,
        tokenizer=tokenizer,
        prompt_len_ms=prompt_ms,
    )
    prompt_len = len(prompt)
    max_new_tokens = min(8096 - prompt_len, length)

    print(f"Generating with Aria (prompt={prompt_len} tokens, "
          f"gen={max_new_tokens} tokens, temp={temp})...")

    results = sample_batch(
        model=model,
        tokenizer=tokenizer,
        prompt=prompt,
        num_variations=variations,
        max_new_tokens=max_new_tokens,
        temp=temp,
        top_p=top_p,
        min_p=min_p if top_p is None else None,
    )

    # Repair malformed token sequences, then detokenize
    tokenized_seq = results[0]
    tokenized_seq = _repair_tokens(tokenized_seq)
    res_midi_dict = tokenizer.detokenize(tokenized_seq)

    # Filter out notes that fall within the prompt duration
    res_midi_dict.note_msgs = [
        msg for msg in res_midi_dict.note_msgs
        if res_midi_dict.tick_to_ms(msg["data"]["start"]) > prompt_ms
    ]
    res_midi_dict.pedal_msgs = [
        msg for msg in res_midi_dict.pedal_msgs
        if res_midi_dict.tick_to_ms(msg["tick"]) > prompt_ms
    ]

    res_midi = res_midi_dict.to_midi()

    # Two-step compression to match AMT-like density:
    # Step 1: Cap large inter-note gaps (from Aria's <T> time tokens)
    #   This eliminates the sparsity without distorting tight note clusters.
    # Step 2: Uniform scale to max_duration if still too long.
    _compress_gaps(res_midi, max_gap_sec=0.3)
    if max_duration and res_midi.length > max_duration:
        scale = max_duration / res_midi.length
        for track in res_midi.tracks:
            for msg in track:
                msg.time = max(0, int(msg.time * scale))

    res_midi.save(output_path)

    note_count = sum(1 for t in res_midi.tracks for m in t
                     if m.type == 'note_on' and m.velocity > 0)
    duration = mido.MidiFile(output_path).length
    print(f"  Output: {output_path} ({note_count} notes, "
          f"{duration:.1f}s)")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Aria Pipeline: MIDI → Aria model → MIDI")
    parser.add_argument("input", help="Path to input .mid file")
    parser.add_argument("--length", type=int, default=2048,
                        help="Number of tokens to generate (default: 2048)")
    parser.add_argument("--prompt-duration", type=float, default=10,
                        help="Seconds of input to use as prompt (default: 10)")
    parser.add_argument("--temp", type=float, default=0.98,
                        help="Sampling temperature (default: 0.98)")
    parser.add_argument("--min-p", type=float, default=0.035,
                        help="Min-p sampling threshold (default: 0.035)")
    parser.add_argument("--top-p", type=float, default=None,
                        help="Top-p sampling threshold (default: None, uses min-p)")
    parser.add_argument("--variations", type=int, default=1,
                        help="Number of variations to generate (default: 1)")
    parser.add_argument("--max-duration", type=float, default=30,
                        help="Max output duration in seconds (default: 30)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output MIDI path (default: <input>_aria.mid)")
    parser.add_argument("--debug", action="store_true",
                        help="Verbose output")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    is_midi = input_path.lower().endswith(('.mid', '.midi'))
    is_logicx = input_path.lower().endswith('.logicx') or os.path.isdir(
        os.path.join(input_path, 'Alternatives'))

    if not is_midi and not is_logicx:
        print(f"Error: {input_path} is not a .mid or .logicx file")
        sys.exit(1)

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = args.output or os.path.join(
        os.path.dirname(input_path), base_name + "_aria.mid")

    # If .logicx, extract MIDI first
    midi_path = input_path
    tmp_midi_path = None
    if is_logicx:
        sys.path.insert(0, os.path.dirname(__file__))
        import extract_midi
        project_data_path = os.path.join(
            input_path, "Alternatives", "000", "ProjectData")
        with open(project_data_path, 'rb') as f:
            data = f.read()
        meta = extract_midi.read_metadata(input_path)
        notes = extract_midi.extract_midi_from_projectdata(data)
        if not notes:
            print("Error: no MIDI notes found in project")
            sys.exit(1)
        tmp = tempfile.NamedTemporaryFile(
            suffix='.mid', delete=False, prefix='aria_logicx_')
        extract_midi.write_midi(notes, tmp.name, meta)
        tmp_midi_path = tmp.name
        midi_path = tmp_midi_path
        print(f"Extracted MIDI from Logic project: {len(notes)} notes")

    mid = mido.MidiFile(midi_path)
    note_count = sum(1 for t in mid.tracks for m in t
                     if m.type == 'note_on' and m.velocity > 0)
    print(f"Input: {input_path}")
    print(f"  {note_count} notes, {mid.length:.1f}s")

    generate(
        midi_path, output_path,
        length=args.length,
        prompt_duration=args.prompt_duration,
        temp=args.temp,
        min_p=args.min_p,
        top_p=args.top_p,
        variations=args.variations,
        max_duration=args.max_duration,
        debug=args.debug,
    )

    if tmp_midi_path:
        os.unlink(tmp_midi_path)

    print(f"\nDone. Output: {output_path}")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        print(f"\nERROR: {e}")
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)
