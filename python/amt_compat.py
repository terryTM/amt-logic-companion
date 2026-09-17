"""Sampling temperature for the stock `anticipation` package.

Upstream AMT's samplers take only `top_p`.  Temperature is applied by scaling
the logits on their way into `sample.nucleus`, i.e. after the time and
instrument masks and before nucleus filtering.
"""
import contextlib


@contextlib.contextmanager
def sampling_temperature(temperature):
    from anticipation import sample

    if temperature == 1.0:
        yield
        return

    original = sample.nucleus
    t = max(float(temperature), 1e-6)
    sample.nucleus = lambda logits, top_p: original(logits / t, top_p)
    try:
        yield
    finally:
        sample.nucleus = original
