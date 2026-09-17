"""Recover per-region track identity (channel strip, instrument, patch) from
a Logic Pro .logicx ProjectData file.  Read-only.

Record stream (reverse-engineered, Logic 10.7 and 12.0):
  Every record is a 36-byte header followed by a payload.
    +0   4cc tag, byte-reversed ('qSvE' = EvSq, 'ivnE' = Envi, ...)
    +10  u16 object id
    +14  u16 channel-strip index   (UCuA / OCuA only)
    +18  u16 sub-record slot       (UCuA only; numbering differs by version)
    +28  u32 payload size
  Records are contiguous: next = off + 36 + size.

  ivnE (environment object = mixer channel strip), payload:
    +148 u16  unknown
    +155 u8   object class (0x09 software instrument, 0x10 audio, 0x00 aux)
    +158 u16  name length, +160 name bytes (padded to even length)
    after name: u16 strip reference = OCuA/UCuA strip index + 1

  OCuA (channel-strip header), payload:
    +4 u8 strip type (0x40 audio, 0x41 input, 0x42 aux, 0x43 instrument)
    default strip name (' Inst 1') follows as a C string

  UCuA (channel-strip content), payload:
    +4 u16 kind: 1 = plug-in, 5 = patch info, 7 = archived settings
    kind 1: +6 u16 insert position (0 on an instrument strip = the
            instrument slot), +14 C-string preset/sample file,
            +120 C-string plug-in name, +132..+143 AU codes (byte-reversed)
    kind 5: +16 C-string patch name, +80 C-string patch category

  Arrangement placement (80-byte events inside the song's EvSq payload):
    +0  u32 0x20 = MIDI region (0x24 = audio region)
    +4  u32 position (960 PPQ, 34560 = bar 1)
    +8  u32 source sequence id (differs from +32 for aliases)
    +16 u32 ivnE id of the track's channel strip
    +20 u8  1-based track ordinal, +23 u8 0x89
    +32 u32 this region's own sequence id  (== list_evsq_regions sub_id >> 16)
"""
import os
import re
import struct
from urllib.parse import unquote

HDR = 36

DRUM_PLUGINS = {
    'drum kit designer', 'drum machine designer', 'ultrabeat', 'drummer',
    'battery', 'addictive drums', 'superior drummer', 'ez drummer',
    'ezdrummer', 'bfd', 'geist', 'impact xt', 'xo',
}
DRUM_CATEGORY = re.compile(r'drum|percussion|\bkit\b|beat', re.I)
DRUM_NAME = re.compile(
    r'drum|\bkit\b|kick|snare|hi-?hat|\bhats?\b|clap|perc|cymbal|\btoms?\b|'
    r'shaker|rimshot|808 kit|909|trap kit', re.I)

# Patch category / name keyword -> GM program (first match wins).
GM_RULES = [
    (r'electric piano|e-piano|rhodes|wurli|\bep\b', 4),
    (r'clav', 7),
    (r'bell|celesta|glock', 9),
    (r'vibraphone|vibes|marimba|mallet|xylo', 11),
    (r'organ|b3', 16),
    (r'piano|grand|keyscape', 0),
    (r'nylon|classical guitar|plucked', 24),
    (r'electric guitar|clean guitar', 27),
    (r'guita', 25),
    (r'808|sub ?bass|synth bass', 38),
    (r'upright|acoustic bass|contrabass', 32),
    (r'bass', 33),
    (r'string|violin|cello|viola', 48),
    (r'choir|vocal|voice|aah', 52),
    (r'trumpet|brass|horn|trombone', 61),
    (r'sax', 65),
    (r'flute|woodwind', 73),
    (r'lead', 81),
    (r'pad|drifting|ambient|atmos', 89),
    (r'arp|pluck|synth', 81),
    (r'keys|keyboard', 4),
]


def _cstr(b, start, limit=128):
    end = b.find(b'\x00', start, start + limit)
    if end == -1:
        end = start + limit
    return b[start:end].decode('utf-8', 'replace')


SAMPLE_RE = re.compile(
    rb'([\x20-\x7e]{1,120}?\.(?:wav|aiff?|caf|mp3|m4a|flac))\x00', re.I)


def _sample_names(blob):
    """Sample file basenames referenced by sampler state (Quick Sampler
    stores them inline; 'TBOS' is a length/marker prefix)."""
    names = []
    for m in SAMPLE_RE.finditer(blob):
        n = os.path.basename(m.group(1).decode('latin1'))
        n = re.sub(r'^.*TBOS', '', n).strip()
        n = re.sub(r'^History zone \((.*)$', r'\1', n)
        n = unquote(n)
        if n and n not in names:
            names.append(n)
    return names


def _exs_samples(logicx_path, preset):
    """Sample names from an .exs instrument saved inside the package."""
    if not preset or not preset.lower().endswith('.exs'):
        return []
    exs = os.path.join(logicx_path, 'Media', 'Sampler Instruments', preset)
    try:
        with open(exs, 'rb') as f:
            return _sample_names(f.read())
    except OSError:
        return []


def walk_records(data):
    """Yield (offset, tag, payload_size) for the contiguous record stream."""
    m = re.search(rb'qSvE|tSnI|qeSM|karT', data)
    if not m:
        return
    i = m.start()
    while i + HDR <= len(data):
        tag = data[i:i + 4]
        if not re.fullmatch(rb'[A-Za-z0-9 #]{4}', tag):
            break
        size = struct.unpack_from('<I', data, i + 28)[0]
        yield i, tag.decode('ascii'), size
        i += HDR + size


def _parse_strips(data, recs):
    envi, ocua, ucua = {}, {}, {}
    for off, tag, size in recs:
        body = data[off + HDR:off + HDR + size]
        if tag == 'ivnE' and size >= 162:
            oid = struct.unpack_from('<H', data, off + 10)[0]
            n = struct.unpack_from('<H', body, 158)[0]
            name = body[160:160 + n].decode('utf-8', 'replace')
            p = 160 + n + (n & 1)
            ref = struct.unpack_from('<H', body, p)[0] if p + 2 <= size else 0
            envi[oid] = {'name': name, 'strip_ref': ref, 'cls': body[155]}
        elif tag == 'OCuA' and size >= 8:
            idx = struct.unpack_from('<H', data, off + 14)[0]
            m = re.search(rb' ([\x20-\x7e]{2,})\x00', body)
            ocua[idx] = {'type': body[4],
                         'default_name': m.group(1).decode() if m else ''}
        elif tag == 'UCuA' and size >= 8:
            idx = struct.unpack_from('<H', data, off + 14)[0]
            kind = struct.unpack_from('<H', body, 4)[0]
            s = ucua.setdefault(idx, {'plugins': [], 'patch': None,
                                      'category': None})
            if kind == 1 and size >= 144:
                s['plugins'].append({
                    'insert': struct.unpack_from('<H', body, 6)[0],
                    'preset': _cstr(body, 14, 100),
                    'name': _cstr(body, 120, 12),
                    'au': body[132:144][::-1].decode('latin1'),
                    'samples': _sample_names(body),
                })
            elif kind == 5 and size >= 96:
                s['patch'] = _cstr(body, 16, 64) or None
                s['category'] = _cstr(body, 80, 64) or None
    return envi, ocua, ucua


def _region_placements(data, recs, region_ids, envi):
    """Map region sequence id -> (ivnE id, track ordinal, position)."""
    out = {}
    for off, tag, size in recs:
        if tag != 'qSvE' or size < 80:
            continue
        b = data[off + HDR:off + HDR + size]
        for i in range(0, len(b) - 36, 4):
            if (b[i] == 0x20 and b[i + 1:i + 4] == b'\0\0\0'
                    and b[i + 23] == 0x89 and b[i + 21:i + 23] == b'\0\0'):
                pos, src = struct.unpack_from('<II', b, i + 4)
                ref = struct.unpack_from('<I', b, i + 16)[0]
                own = struct.unpack_from("<I", b, i + 32)[0]
                if own in region_ids and ref in envi:
                    out.setdefault(own, (ref, b[i + 20], pos, src))
    return out


def _gm_program(*texts):
    blob = ' '.join(t for t in texts if t)
    for pat, prog in GM_RULES:
        if re.search(pat, blob, re.I):
            return prog
    return None


def _classify(strip_name, inst, patch, category, oc_type, samples=None):
    """Return (is_drum, evidence)."""
    if oc_type is not None and oc_type != 0x43:
        return None, 'not an instrument strip'
    if inst:
        if inst['name'].lower() in DRUM_PLUGINS:
            return True, f"plugin {inst['name']}"
        if DRUM_NAME.search(inst['preset'] or ''):
            return True, f"instrument preset {inst['preset']!r}"
    if samples and DRUM_NAME.search(samples):
        return True, f'sample {samples!r}'
    if category and DRUM_CATEGORY.search(category):
        return True, f'patch category {category!r}'
    if patch and DRUM_NAME.search(patch):
        return True, f'patch name {patch!r}'
    if strip_name and DRUM_NAME.search(strip_name):
        return True, f'strip name {strip_name!r}'
    if inst or patch or category:
        return False, 'instrument/patch present, no drum evidence'
    return None, 'no instrument info'


def read_track_instruments(logicx_path):
    """{(track_id, sub_id): {'track_name', 'patch_name', 'plugin',
    'is_drum', 'gm_program', 'evidence', ...}} for every note-bearing region.
    """
    import extract_midi

    pd = os.path.join(logicx_path, 'Alternatives', '000', 'ProjectData')
    with open(pd, 'rb') as f:
        data = f.read()
    recs = list(walk_records(data))
    envi, ocua, ucua = _parse_strips(data, recs)
    regions = extract_midi.list_evsq_regions(data)
    placements = _region_placements(
        data, recs, {r['sub_id'] >> 16 for r in regions}, envi)

    out = {}
    for r in regions:
        key = (r['track_id'], r['sub_id'])
        seq = r['sub_id'] >> 16
        pl = placements.get(seq)
        if pl is None:
            out[key] = {'track_name': None, 'patch_name': None,
                        'plugin': None, 'is_drum': None, 'gm_program': None,
                        'track_ordinal': None, 'position': None,
                        'evidence': 'region not placed in arrangement'}
            continue
        env_id, ordinal, pos, _src = pl
        env = envi.get(env_id, {})
        strip_idx = env.get('strip_ref', 0) - 1
        oc = ocua.get(strip_idx, {})
        uc = ucua.get(strip_idx, {'plugins': [], 'patch': None,
                                  'category': None})
        inst = None
        if oc.get('type') == 0x43:
            inst = next((p for p in uc['plugins'] if p['insert'] == 0), None)
        samples = []
        if inst:
            samples = inst['samples'] or _exs_samples(logicx_path,
                                                      inst['preset'])
        # First reference is taken as the loaded sample; later ones are
        # usually Quick Sampler history.  Heuristic.
        sample_txt = samples[0] if samples else None
        # Mimic the arrange-window label: an unnamed ('#default') strip shows
        # its loaded instrument file (e.g. 'Instrument #68' for an .exs),
        # else the default strip name ('Inst 3').
        name = env.get('name')
        if not name or name.startswith('#'):
            preset = inst['preset'] if inst else ''
            if preset and not preset.startswith('#'):
                name = os.path.splitext(preset)[0]
            else:
                name = oc.get('default_name') or name
        is_drum, why = _classify(env.get('name'), inst, uc['patch'],
                                 uc['category'], oc.get('type'), sample_txt)
        gm = None if is_drum else _gm_program(
            uc['category'], uc['patch'],
            inst['preset'] if inst else None,
            sample_txt, inst['name'] if inst else None, env.get('name'))
        out[key] = {
            'track_name': name,
            'patch_name': uc['patch'],
            'category': uc['category'],
            'plugin': inst['name'] if inst else None,
            'samples': samples,
            'is_drum': is_drum,
            'gm_program': gm,
            'track_ordinal': ordinal,
            'position': pos,
            'evidence': (f"ivnE {env_id:#x} -> strip {strip_idx} "
                         f"({oc.get('default_name', '?')}); {why}"),
        }
    return out


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    for path in sys.argv[1:]:
        print('==', path)
        for (tid, sid), v in read_track_instruments(path).items():
            print(f"  ({tid},{sid:#x}) ord={v['track_ordinal']} "
                  f"name={v['track_name']!r} plugin={v['plugin']!r} "
                  f"patch={v['patch_name']!r} cat={v.get('category')!r} "
                  f"samples={v.get('samples')!r} "
                  f"drum={v['is_drum']} gm={v['gm_program']} | {v['evidence']}")
