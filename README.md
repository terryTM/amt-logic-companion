# AMT Logic Companion

A macOS companion app that generates MIDI parts for a Logic Pro session with the
[Anticipatory Music Transformer](https://github.com/jthickstun/anticipation)
(AMT).

Drop a saved `.logicx` project (or a `.mid` file) onto the window. The app reads
the note regions out of Logic's undocumented `ProjectData` file, lets you pick
and audition a track, generates new material, and shows the result as a piano
roll. Drag the result, or a single generated part, straight onto a Logic track.
The app only reads the project and never modifies it.

> **Research prototype.** Logic's project format was reverse-engineered from
> Logic Pro 10.7 and 12.0 projects and may break with other releases.

## Requirements

- macOS 13 or later; Apple Silicon recommended (AMT runs on MPS)
- Swift 5.9+ (Xcode or the Command Line Tools)
- Python 3.11

## Setup

```bash
git clone https://github.com/<you>/amt-logic-companion.git
cd amt-logic-companion

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Model weights download from Hugging Face on first use
(`stanford-crfm/music-{small,medium,large}-800k`).

## Run the app

```bash
cd app
swift run
```

The app finds `python/` and `.venv/` by walking up from its executable, so it
works from a clone without configuration. To use a different setup:

| Variable | Purpose |
|---|---|
| `AMT_PYTHON` | Python interpreter to use (default: `.venv/bin/python`, then `python3`) |
| `AMT_PYTHON_DIR` | Folder containing the pipeline scripts (default: `python/`) |
| `AMT_MODEL_SMALL` / `_MEDIUM` / `_LARGE` | Local directory for an AMT checkpoint instead of downloading it |
| `AMT_NO_KV_CACHE` | Set to `1` to sample without the KV cache (slower; see `python/kv_sample.py`) |

## Command line

The app is a front end for the scripts in `python/`, which also run on their own:

```bash
cd python

# List the note regions in a project as JSON
../.venv/bin/python amt_pipeline.py Song.logicx --list-tracks

# Continue one region with AMT (what the app runs)
../.venv/bin/python amt_pipeline.py Song.logicx --no-inject --model-size small \
    --track-id 23 --sub-id 0x00500000 --seed 7 --output out.mid

# Anticipatory accompaniment: generate the first region, using the others as controls
../.venv/bin/python anticipatory.py Song.logicx --seed 42 --output accompaniment.mid

# ...or pick the target and context regions, and override instrument labels
# (GM program 0-127, 128 = drums); this is what the app runs in Accompany mode
../.venv/bin/python anticipatory.py Song.logicx --target 23:0x002C0000 \
    --parts 23:0x00300000 23:0x00440000 --instr 23:0x00440000=33 \
    --output accompaniment.mid

# Inspect the channel strip, instrument and patch behind each region
../.venv/bin/python logic_instruments.py Song.logicx
```

Omit `--no-inject` in `amt_pipeline.py` only on a copy of a project: injection
writes generated notes back into `ProjectData` (a backup is kept) and is
experimental.

## Repository layout

```
app/                    SwiftUI app (Swift Package)
python/
  extract_midi.py       ProjectData parser: note regions, tempo, key, meter
  logic_instruments.py  Region → track, channel strip, plug-in, patch
  drum_detect.py        Drum detection from note content alone
  amt_pipeline.py       AMT continuation pipeline used by the app
  anticipatory.py       AMT anticipatory (accompaniment) generation
  instrument_assign.py  Instrument label per part (metadata, drums, placeholder)
  amt_compat.py         Sampling temperature for the stock anticipation package
  inject_midi.py        Experimental write-back into ProjectData
tests/                  python -m unittest discover tests
```

## How parts are labelled for AMT

AMT tokens combine instrument and pitch, and drums are their own instrument.
Logic regions don't carry a MIDI program, so `instrument_assign.py` gives each
part a label: from its channel strip (plug-in, patch or sample name, via
`logic_instruments.py`) when possible, else drums if `drum_detect.py` finds a
kit or hi-hat loop in the notes, else a distinct placeholder program. The app
shows each label and its source, and any label can be changed from the track
list before generating. Labels only affect what AMT sees, never the project.

## Reproducibility

`requirements.txt` pins the versions the demo was tested with. With those
versions, a fixed `--seed` reproduces AMT output exactly. Newer torch or
transformers releases run fine but sample different notes.

Sampling reuses a KV cache across the three tokens of an event
(`python/kv_sample.py`), which is what the model would have computed anyway but
on a different kernel path, so logits differ by ~1e-3 in float32. In testing a
fixed seed still reproduced the same notes, but it is no longer guaranteed to.
Set `AMT_NO_KV_CACHE=1` to sample the original way.

## Acknowledgements and licenses

This project is MIT-licensed (see `LICENSE`). It depends on, but does not
include:

- [anticipation](https://github.com/jthickstun/anticipation) (Apache-2.0),
  Thickstun et al., *Anticipatory Music Transformer*, TMLR 2024

Model weights are covered by the terms on their Hugging Face model cards.
