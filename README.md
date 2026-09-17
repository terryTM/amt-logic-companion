# AMT Logic Companion

A macOS companion app that generates MIDI parts for a Logic Pro session with the
[Anticipatory Music Transformer](https://github.com/jthickstun/anticipation)
(AMT) or [Aria](https://github.com/EleutherAI/aria).

Drop a saved `.logicx` project (or a `.mid` file) onto the window. The app reads
the note regions out of Logic's undocumented `ProjectData` file, lets you pick
and audition a track, generates new material, and shows the result as a piano
roll. Drag the result, or a single generated part, straight onto a Logic track.
The app only reads the project and never modifies it.

> **Research prototype.** Logic's project format was reverse-engineered from
> Logic Pro 10.7 and 12.0 projects and may break with other releases.

## Requirements

- macOS 13 or later; Apple Silicon recommended (AMT runs on MPS, Aria on MLX)
- Swift 5.9+ (Xcode or the Command Line Tools)
- Python 3.11

## Setup

```bash
git clone https://github.com/<you>/amt-logic-companion.git
cd amt-logic-companion

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt        # AMT only
.venv/bin/pip install -r requirements-aria.txt   # AMT + Aria (optional)
```

Model weights download from Hugging Face on first use
(`stanford-crfm/music-{small,medium,large}-800k`, and `loubb/aria-medium-base`
for Aria).

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
| `ARIA_CHECKPOINT` | Local `model-gen.safetensors` for Aria |

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
  aria_pipeline.py      Aria generation (MLX)
  amt_compat.py         Sampling temperature for the stock anticipation package
  inject_midi.py        Experimental write-back into ProjectData
tests/                  python -m unittest discover tests
```

## How parts are labelled for AMT

AMT tokens combine instrument and pitch, and drums are their own instrument.
Logic regions don't carry a MIDI program, so parts get placeholder programs,
and a part whose notes look like drums (a General MIDI kit, or a sampler
hi-hat loop with rolls) is routed to the drum channel instead. `drum_detect.py`
is a heuristic; `logic_instruments.py` recovers the actual instrument from the
project and is the more reliable source.

## Reproducibility

`requirements.txt` pins the versions the demo was tested with. With those
versions, a fixed `--seed` reproduces AMT output exactly. Newer torch or
transformers releases run fine but sample different notes.

## Acknowledgements and licenses

This project is MIT-licensed (see `LICENSE`). It depends on, but does not
include:

- [anticipation](https://github.com/jthickstun/anticipation) (Apache-2.0),
  Thickstun et al., *Anticipatory Music Transformer*, TMLR 2024
- [aria](https://github.com/EleutherAI/aria) (Apache-2.0), EleutherAI

Model weights are covered by the terms on their Hugging Face model cards.
