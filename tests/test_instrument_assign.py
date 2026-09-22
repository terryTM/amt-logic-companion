"""Unit tests for instrument_assign (no Logic project or model needed).

Run from the repo root:  python -m unittest discover tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

import instrument_assign as ia  # noqa: E402

TPB = 480


def region(track_id, sub_id, notes):
    return {'track_id': track_id, 'sub_id': sub_id, 'notes': notes}


def melody():
    return [(i * TPB, 60 + (i % 5), 90, TPB) for i in range(12)]


def kit():
    notes = []
    for bar in range(2):
        b = bar * 4 * TPB
        notes += [(b + i * TPB // 2, 42, 90, 60) for i in range(8)]
        notes += [(b, 36, 100, 60), (b + 2 * TPB, 36, 100, 60)]
        notes += [(b + TPB, 38, 100, 60), (b + 3 * TPB, 38, 100, 60)]
    return sorted(notes)


class AssignInstrumentsTest(unittest.TestCase):

    def assign(self, regions, meta):
        with mock.patch('logic_instruments.read_track_instruments',
                        return_value=meta):
            return ia.assign_instruments('Song.logicx', regions)

    def test_metadata_wins(self):
        regions = [region(23, 0x10000, melody()), region(23, 0x20000, kit())]
        meta = {
            (23, 0x10000): {'gm_program': 33, 'is_drum': False,
                            'plugin': 'Sampler', 'patch_name': 'Fingered Bass'},
            (23, 0x20000): {'gm_program': None, 'is_drum': True,
                            'plugin': 'Drum Kit Designer'},
        }
        out = self.assign(regions, meta)
        self.assertEqual([o['instr'] for o in out], [33, ia.DRUMS])
        self.assertEqual({o['source'] for o in out}, {'metadata'})
        self.assertEqual(out[0]['evidence'], 'Sampler / Fingered Bass')

    def test_note_heuristic_finds_drums_without_metadata(self):
        out = self.assign([region(23, 0x10000, kit())], {})
        self.assertEqual(out[0]['instr'], ia.DRUMS)
        self.assertEqual(out[0]['source'], 'notes')

    def test_placeholders_are_distinct_and_avoid_metadata(self):
        regions = [region(23, 0x10000 * (i + 1), melody()) for i in range(4)]
        meta = {(23, 0x10000): {'gm_program': ia.PLACEHOLDERS[0],
                                'is_drum': False}}
        out = self.assign(regions, meta)
        instrs = [o['instr'] for o in out]
        self.assertEqual(len(set(instrs)), 4)
        self.assertEqual([o['source'] for o in out[1:]], ['placeholder'] * 3)
        self.assertEqual(out, self.assign(regions, meta))    # deterministic

    def test_unreadable_metadata_degrades_to_placeholder(self):
        with mock.patch('logic_instruments.read_track_instruments',
                        side_effect=ValueError('bad record')):
            out = ia.assign_instruments('Song.logicx',
                                        [region(23, 0x10000, melody())])
        self.assertEqual(out[0]['source'], 'placeholder')
        self.assertIn('bad record', out[0]['evidence'])

    def test_region_key_round_trip(self):
        key = ia.region_key(23, 0x00500000)
        self.assertEqual(key, '23:0x00500000')
        self.assertEqual(ia.parse_region_key(key), (23, 0x00500000))


if __name__ == '__main__':
    unittest.main()
