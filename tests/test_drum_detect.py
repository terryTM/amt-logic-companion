"""Unit tests for drum_detect on synthetic parts (standard library only).

Run from the repo root:  python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

from drum_detect import classify_part  # noqa: E402

TPB = 480
EIGHTH = TPB // 2


def note(beat, pitch, dur=EIGHTH // 2, vel=100):
    return (int(beat * TPB), pitch, vel, int(dur))


class ClassifyPartTest(unittest.TestCase):

    def test_gm_kit(self):
        notes = []
        for bar in range(2):
            b = bar * 4
            notes += [note(b + i / 2, 42) for i in range(8)]     # closed hat
            notes += [note(b, 36), note(b + 2, 36)]              # kick
            notes += [note(b + 1, 38), note(b + 3, 38)]          # snare
        is_drum, why = classify_part(sorted(notes))
        self.assertTrue(is_drum, why)
        self.assertTrue(why.startswith('gm_kit'), why)

    def test_sampler_hat_loop_with_rolls(self):
        notes = []
        for bar in range(2):
            b = bar * 4
            notes += [note(b + i / 2, 60, dur=EIGHTH) for i in range(6)]
            # 32nd-note roll pitched down from the main key
            notes += [note(b + 3 + i / 8, 58 if i % 2 else 60, dur=EIGHTH)
                      for i in range(8)]
        is_drum, why = classify_part(sorted(notes))
        self.assertTrue(is_drum, why)
        self.assertTrue(why.startswith('one_shot'), why)

    def test_three_note_melody_is_pitched(self):
        notes = [note(i * 0.75, [60, 62, 64][i % 3], dur=420) for i in range(14)]
        self.assertFalse(classify_part(notes)[0])

    def test_chordal_piano_is_pitched(self):
        notes = []
        for i, root in enumerate([48, 53, 55, 48]):
            for p in (root, root + 4, root + 7, root + 12):
                notes.append(note(i * 2, p, dur=TPB * 2))
        self.assertFalse(classify_part(notes)[0])

    def test_bass_on_drum_keys_is_pitched(self):
        # Monophonic line that happens to sit on kick/snare/hat keys.
        line = [36, 38, 40, 42, 43, 42, 40, 38] * 2
        notes = [note(i / 2, p, dur=EIGHTH) for i, p in enumerate(line)]
        self.assertFalse(classify_part(notes)[0])

    def test_too_few_notes(self):
        is_drum, why = classify_part([note(0, 36), note(1, 38)])
        self.assertFalse(is_drum)
        self.assertIn('too few', why)


if __name__ == '__main__':
    unittest.main()
