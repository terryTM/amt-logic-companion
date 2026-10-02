"""Unit tests for kv_sample (tiny randomly-initialised model, no checkpoint).

Run from the repo root:  python -m unittest discover tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

try:
    import torch
    from transformers import GPT2Config, GPT2LMHeadModel
    from anticipation import sample as _sample
    from anticipation.vocab import (AUTOREGRESS, DUR_OFFSET, NOTE_OFFSET,
                                    TIME_OFFSET, VOCAB_SIZE)
    import kv_sample
    from amt_compat import sampling_temperature
except ImportError as exc:  # torch/transformers/anticipation not installed
    raise unittest.SkipTest(f"dependencies unavailable: {exc}")


def tiny_model():
    """A small causal LM over the AMT vocabulary, on CPU for determinism."""
    torch.manual_seed(0)
    config = GPT2Config(vocab_size=VOCAB_SIZE, n_positions=1024, n_embd=32,
                        n_layer=2, n_head=2)
    return GPT2LMHeadModel(config).eval()


def event_tokens(n):
    """`n` well-formed (time, duration, note) events on a rising clock."""
    tokens = []
    for i in range(n):
        tokens += [TIME_OFFSET + 10 * i, DUR_OFFSET + 25,
                   NOTE_OFFSET + 60 + (i % 7)]
    return tokens


class KVCacheEquivalence(unittest.TestCase):
    """The cached path must sample what the uncached path would sample.

    Temperature is driven to the floor so the draw is argmax and the
    comparison isn't at the mercy of the RNG.
    """

    def _compare(self, n_events):
        model = tiny_model()
        tokens = event_tokens(n_events)
        current_time = tokens[-3] - TIME_OFFSET

        # The pinned upstream has no temperature argument; amt_compat
        # scales the logits inside nucleus, so do the same here to drive
        # the draw to argmax and keep the comparison off the RNG.
        kwargs = dict(top_p=1.0, current_time=current_time)
        with sampling_temperature(1e-6):
            expected = _sample.add_token(
                model, [AUTOREGRESS], tokens, **kwargs)
            actual = kv_sample.add_token(
                model, [AUTOREGRESS], tokens, **kwargs)
        self.assertEqual(expected, actual)

    def test_short_context(self):
        self._compare(8)

    def test_long_context(self):
        # Past the 1017-token Markov window, so `lookback` and `offset` are
        # both non-trivial.
        self._compare(400)


class Patching(unittest.TestCase):

    def setUp(self):
        self.original = _sample.add_token
        self.addCleanup(setattr, _sample, 'add_token', self.original)

    def test_enable_is_idempotent(self):
        self.assertTrue(kv_sample.enable())
        patched = _sample.add_token
        self.assertFalse(kv_sample.enable())
        self.assertIs(_sample.add_token, patched)

    def test_disable_restores_original(self):
        kv_sample.enable()
        self.assertTrue(kv_sample.disable())
        self.assertIs(_sample.add_token, self.original)
        self.assertFalse(kv_sample.disable())


if __name__ == '__main__':
    unittest.main()
