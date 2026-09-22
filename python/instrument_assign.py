"""Assign each Logic region the AMT instrument it is conditioned as.

AMT tokens pair every pitch with an instrument (a GM program 0-127, or 128 for
drums), so the label decides how the model reads a part.  Labels come from,
in order of preference:

  metadata     -- the region's channel strip (plug-in, patch, sampler files),
                  matched by logic_instruments against keyword rules
  notes        -- drum_detect's note-content heuristic, for drum parts only
  placeholder  -- a deterministic program not used by any other part

The labels only organize AMT's input; they never touch the Logic project.
The companion app shows them and lets the user override any of them.
"""
DRUMS = 128

# Placeholder programs, tried in order; spread across GM families so that two
# unclassified parts never share a label.
PLACEHOLDERS = (
    0, 33, 48, 24, 81, 4, 73, 89, 11, 56, 65, 16, 40, 27, 52, 38,
)


def _hex(sub_id):
    return f"0x{sub_id:08X}"


def region_key(track_id, sub_id):
    """Stable text key for a region, as the app and the CLI spell it."""
    return f"{int(track_id)}:{_hex(int(sub_id))}"


def parse_region_key(text):
    tid, sid = text.split(':', 1)
    return int(tid), int(sid, 16)


def assign_instruments(logicx_path, regions):
    """Return one {'instr', 'source', 'evidence'} dict per region, in order.

    regions: dicts with 'track_id', 'sub_id' and 'notes' ((tick, pitch, vel,
    dur) tuples).  Deterministic for a given project.
    """
    from drum_detect import classify_part

    try:
        from logic_instruments import read_track_instruments
        meta = read_track_instruments(logicx_path)
    except Exception as e:          # undocumented format: degrade, don't fail
        meta = {}
        meta_error = f"channel-strip metadata unreadable ({e})"
    else:
        meta_error = None

    out = [None] * len(regions)
    pending = []
    for i, r in enumerate(regions):
        info = meta.get((r['track_id'], r['sub_id'])) or {}
        # What the user would recognize in Logic: plug-in, then its patch
        # or loaded sample.
        sound = info.get('patch_name') or next(iter(info.get('samples') or []),
                                               None)
        what = ' / '.join(t for t in (info.get('plugin'), sound) if t) \
            or info.get('track_name') or 'channel strip'
        if info.get('is_drum'):
            out[i] = {'instr': DRUMS, 'source': 'metadata', 'evidence': what}
        elif info.get('gm_program') is not None:
            out[i] = {'instr': info['gm_program'], 'source': 'metadata',
                      'evidence': what}
        else:
            is_drum, why = classify_part(r['notes'])
            if is_drum:
                out[i] = {'instr': DRUMS, 'source': 'notes',
                          'evidence': why.split(':')[0] + ' drum pattern'}
            else:
                pending.append(i)

    used = {o['instr'] for o in out if o}
    free = [p for p in PLACEHOLDERS if p not in used]
    for n, i in enumerate(pending):
        prog = free[n] if n < len(free) else 24 + n
        reason = meta_error or 'no instrument metadata'
        out[i] = {'instr': prog, 'source': 'placeholder', 'evidence': reason}
    return out
