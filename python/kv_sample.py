"""KV-cached `add_token` for the anticipation sampler.

`anticipation.sample.add_token` runs a full forward pass for each of the three
tokens in an event, re-encoding the whole ~1018-token Markov window every time
and keeping only the last position's logits.  But the three passes within one
event differ only by a suffix: `z + history` is fixed and `new_token` grows by
one token each pass.  So the first pass can populate a KV cache and the other
two run as single-token decode steps against it.

Measured on AMT small (float32, MPS), continuing a 20 s prompt (138 events,
~415 tokens of context) for 96 notes: 91.5 s becomes 9.4 s, about 9.7x.  That
run sampled an identical token stream, though see the note on numerics below.

What this does NOT change: the Markov window, the time relativization, the
logit masks, or the order in which tokens are sampled.  The model conditions
on exactly the same context it did before.

Caching *across* events would need more than this.  `offset` in `add_token` is
`ops.min_time()` over the window, so it holds steady only while the window
still starts at the same event.  Once `len(tokens)` passes 1017 the window
slides by one event per step, `offset` jumps, and every time token already in
the cache is rewritten -- invalidating it.  Reusing a cache past that point
means choosing a slide policy (evict a block, re-prefill once, then run many
events off the cache), which changes what the model sees at each boundary.

Numerically this is the same model on a different kernel path: a batched GEMM
over the sequence versus a GEMV against cached keys.  Logits agree to ~1e-3 in
float32, which is in principle enough to flip a `torch.multinomial` draw that
sits near a boundary -- nucleus filtering leaves the distribution concentrated
enough that it did not happen in testing, but a fixed `--seed` is no longer
*guaranteed* to reproduce output from before this change.  Set
AMT_NO_KV_CACHE=1 to restore the original path.
"""
import torch
import torch.nn.functional as F

from anticipation import ops
from anticipation import sample as _sample

LOOKBACK = 1017  # Markov window, as in anticipation.sample.add_token


def add_token(model, z, tokens, top_p, current_time, debug=False,
              temperature=1.0):
    """Sample one (time, duration, note) event, reusing one forward pass.

    Drop-in replacement for `anticipation.sample.add_token`.  The pinned
    upstream takes no `temperature` and never passes one -- amt_compat applies
    it by wrapping `sample.nucleus` -- but later upstreams do, so it is
    accepted here and honoured the same way they honour it.
    """
    assert len(tokens) % 3 == 0

    history = tokens.copy()
    lookback = max(len(tokens) - LOOKBACK, 0)
    history = history[lookback:]  # Markov window
    offset = ops.min_time(history, seconds=False)
    history[::3] = [tok - offset for tok in history[::3]]  # relativize time

    new_token = []
    with torch.no_grad():
        # One full pass over `z + history`; the remaining two tokens of this
        # event are single-token decode steps against its cache.
        input_tokens = torch.tensor(z + history).unsqueeze(0).to(model.device)
        out = model(input_tokens, use_cache=True)
        past = out.past_key_values
        logits = out.logits[0, -1]
        idx = input_tokens.shape[1] - 1

        for i in range(3):
            # Resolved through the module so a caller's monkeypatch
            # (amt_pipeline's piano-only instr_logits) still applies.
            logits = _sample.safe_logits(logits, idx)
            if i == 0:
                logits = _sample.future_logits(logits, current_time - offset)
            elif i == 2:
                logits = _sample.instr_logits(logits, tokens)
            if temperature != 1.0:
                logits = logits / max(float(temperature), 1e-6)
            # Also resolved through the module: amt_compat's temperature
            # wrapper replaces `nucleus`.
            logits = _sample.nucleus(logits, top_p)

            probs = F.softmax(logits, dim=-1)
            token = torch.multinomial(probs, 1)
            new_token.append(int(token))

            if i < 2:
                out = model(token.view(1, 1), past_key_values=past,
                            use_cache=True)
                past = out.past_key_values
                logits = out.logits[0, -1]
                idx += 1

    new_token[0] += offset  # revert to full sequence timing
    if debug:
        print(f'  OFFSET = {offset}, LEN = {len(history)}, '
              f'TIME = {tokens[::3][-5:]}')

    return new_token


add_token._kv_cached = True


def enable(debug=False):
    """Patch `anticipation.sample.add_token`.  Idempotent; returns whether it
    changed anything.

    `generate()` and `generate_ar()` resolve `add_token` as a module global at
    call time, so patching the module is enough -- the same mechanism
    amt_pipeline already uses for `instr_logits`.
    """
    if getattr(_sample.add_token, '_kv_cached', False):
        return False
    add_token._original = _sample.add_token
    _sample.add_token = add_token
    if debug:
        print("  KV cache enabled (one forward pass per event, not three)")
    return True


def disable():
    """Restore the original `add_token`.  Returns whether it changed
    anything."""
    original = getattr(_sample.add_token, '_original', None)
    if original is None:
        return False
    _sample.add_token = original
    return True
