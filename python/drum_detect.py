"""Heuristic drum/percussion detection from note content alone.

Logic's ProjectData parser recovers notes but not the instrument playing them,
so a drum region would otherwise reach AMT as a pitched part (its kick/snare/hat
keys read as low-register pitches).  AMT represents drums as instrument 128
(anything on MIDI channel 10), so detected drum parts must go on channel 9.

Two kinds of drum part are recognised:

  gm_kit    -- a General MIDI-mapped kit: kick, snare/clap and cymbal keys all
               present, nearly every note inside the GM percussion range, and
               some kick/snare hits landing together with a cymbal (a bass
               line on the same keys is monophonic).
  one_shot  -- a sampler loop (e.g. a hi-hat one-shot on C3 with pitched rolls):
               one dominant key, rolls pitched at or below it, monophonic.

This is a heuristic.  It cannot see the instrument, so e.g. a tuned 808 is
(correctly) pitched, but a repeated-note line with fast downward ornaments can
look like a hat loop.  The one_shot thresholds were fit to a single 20-loop
trap hi-hat pack.  Callers should let the user override the result.
"""
from collections import Counter

GM_RANGE = (35, 81)
KICK = {35, 36}
BACKBEAT = {37, 38, 39, 40}           # side stick, snare, clap, electric snare
CYMBAL = {42, 44, 46, 49, 51, 57, 59}  # hats, crashes, rides

MIN_NOTES = 8


def classify_part(notes, tpb=480):
    """Return (is_drum, reason) for notes given as (tick, pitch, vel, dur)."""
    n = len(notes)
    if n < MIN_NOTES:
        return False, f"too few notes ({n}) to judge"

    pitches = [p for _, p, _, _ in notes]
    onsets = Counter(t for t, *_ in notes)
    pc = Counter(pitches)

    def frac(keys):
        return sum(pc[k] for k in keys) / n

    in_range = sum(GM_RANGE[0] <= p <= GM_RANGE[1] for p in pitches) / n
    kick, back, cym = frac(KICK), frac(BACKBEAT), frac(CYMBAL)
    # Simultaneous hits that pair a kick/backbeat key with a cymbal key.
    by_onset = {}
    for t, p, _, _ in notes:
        by_onset.setdefault(t, set()).add(p)
    kit_hits = sum(1 for ps in by_onset.values()
                   if ps & (KICK | BACKBEAT) and ps & CYMBAL)
    kit_simul = kit_hits / len(by_onset)

    if (in_range >= 0.95 and kick >= 0.05 and back >= 0.05 and cym >= 0.10
            and kit_simul >= 0.05):
        return True, (f"gm_kit: kick {kick:.2f}, backbeat {back:.2f}, "
                      f"cymbal {cym:.2f}, kit-simul {kit_simul:.2f}")

    # One-shot loops: gate lengths are arbitrary (often a full 8th), so use the
    # rhythm instead -- rolls (gaps <= a 16th-note triplet) pitched downward
    # from one dominant key, played monophonically.
    top_key, top_count = pc.most_common(1)[0]
    top_share = top_count / n
    below = sum(p <= top_key for p in pitches) / n
    starts = sorted(onsets)
    gaps = [(b - a) / tpb for a, b in zip(starts, starts[1:])]
    fast = sum(g <= 1 / 6 + 1e-6 for g in gaps) / len(gaps) if gaps else 0.0
    stacked = 1 - len(onsets) / n     # notes sharing an onset with an earlier one
    if (fast >= 0.15 and top_share >= 0.30 and below >= 0.90
            and stacked <= 0.20):
        return True, (f"one_shot: key {top_key} share {top_share:.2f}, "
                      f"rolls {fast:.2f}, at/below key {below:.2f}")

    return False, (f"pitched: in-GM-range {in_range:.2f}, top key share "
                   f"{top_share:.2f}, rolls {fast:.2f}")
