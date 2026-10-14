"""TranslateGemma-4B (MLX, 4-bit) source->target translation — the only model.

Word translations come from the glossary (frequency-deck definitions +
WikDict) filled by the candidates stage; this module only handles:
- full sentences -> natural, idiomatic target language
- glossary misses -> single-word fallback (dominant-sense caveat; the
  review screen allows inline editing).

Apple-native MLX: no daemon; one model in memory; caller unloads / process
exits after the stage.
"""
from mlx_lm import generate as _generate
from mlx_lm import load as _mlx_load
from mlx_lm.sample_utils import make_sampler

import config

MODEL = "mlx-community/translategemma-4b-it-4bit"

_mod = None
_tok = None


def load():
    global _mod, _tok
    if _mod is None:
        _mod, _tok = _mlx_load(MODEL)
    return _mod, _tok


def unload():
    """Release model memory. Call when the stage is done."""
    global _mod, _tok
    _mod = None
    _tok = None


def _run(user_msg: str, max_tokens: int) -> str:
    load()
    messages = [{
        "role": "user",
        "content": [{
            "type": "text",
            "source_lang_code": config.SOURCE,
            "target_lang_code": config.TARGET,
            "text": user_msg,
        }],
    }]
    prompt = _tok.apply_chat_template(
        messages, add_generation_prompt=True
    )
    # TranslateGemma ends turns with <end_of_turn>, not the tokenizer's
    # <eos>; mlx-lm does not stop there, so cut in post-processing.
    out = _generate(
        _mod, _tok, prompt=prompt,
        max_tokens=max_tokens,
        sampler=make_sampler(temp=0.0),
        verbose=False,
    )
    for stop in ("<end_of_turn>", "<eos>"):
        out = out.split(stop)[0]
    return out.strip().strip('"')


def _translate_all(user_msgs: list[str], max_tokens: int,
                   progress=None) -> list[str]:
    out = []
    for i, msg in enumerate(user_msgs, 1):
        out.append(_run(msg, max_tokens))
        if progress:
            progress(i, len(user_msgs))
    return out


def translate(texts: list[str], progress=None) -> list[str]:
    """Full sentences -> natural, idiomatic target language."""
    return _translate_all(texts, 256, progress)


def translate_words(items: list[tuple[str, str]], progress=None) -> list[str]:
    """Glossary misses only: single-word fallback with sentence context
    in the message for provenance (TranslateGemma mostly ignores it)."""
    return _translate_all([s for s, _ in items], 24, progress)
