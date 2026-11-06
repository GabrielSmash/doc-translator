"""Opus-MT models running on CTranslate2 (CPU or CUDA)."""

import ctypes
import logging
import re
from pathlib import Path

import ctranslate2
import sentencepiece as spm

from .base import Glossary, Unit, UnitTranslation, protect, restore, translate_independently

log = logging.getLogger(__name__)

# Splits after sentence punctuation followed by an uppercase letter. Opus-MT is
# trained on sentences, so long paragraphs translate better split up.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
BATCH_SIZE = 32
UNKNOWN = "⁇"  # what SentencePiece decodes unknown tokens to


class OpusTranslator:
    def __init__(
        self,
        model_dir: Path,
        target_token: str,
        device: str = "auto",
        glossary: Glossary | None = None,
    ):
        self.target_token = target_token
        # Glossary terms are swapped for placeholders: kept as-is or replaced
        # by their fixed translation after the model runs.
        self.glossary = glossary
        self.source_sp = spm.SentencePieceProcessor(str(model_dir / "source.spm"))
        self.target_sp = spm.SentencePieceProcessor(str(model_dir / "target.spm"))
        self.model_dir = model_dir
        self._unknown_cache: dict[str, bool] = {}
        if device == "auto":
            self._load_auto()
        else:
            self._load(device)

    def _load(self, device: str) -> None:
        if device == "cuda":
            _preload_cuda_libraries()
        compute_type = "int8_float16" if device == "cuda" else "int8"
        self.model = ctranslate2.Translator(
            str(self.model_dir), device=device, compute_type=compute_type
        )
        self.device = device
        log.info("Opus-MT loaded on %s (%s)", device, compute_type)

    def _load_auto(self) -> None:
        if ctranslate2.get_cuda_device_count() > 0:
            try:
                self._load("cuda")
                # CUDA libraries are only loaded lazily, on the first call.
                self._translate_sentences(["Hello."])
                return
            except RuntimeError as error:
                log.warning("GPU unavailable (%s); falling back to CPU", error)
        self._load("cpu")

    def translate_units(self, units: list[Unit]) -> list[UnitTranslation]:
        return translate_independently(self.translate, units)

    def translate(self, texts: list[str]) -> list[str]:
        # Flatten every text into sentences, translate unique sentences once,
        # then rebuild each text.
        split = [SENTENCE_END.split(text.strip()) for text in texts]
        unique = list(dict.fromkeys(s for sentences in split for s in sentences if s))
        translated = dict(zip(unique, self._translate_sentences(unique)))
        return [" ".join(translated.get(s, s) for s in sentences) for sentences in split]

    def _translate_sentences(self, sentences: list[str]) -> list[str]:
        # The target vocabulary lacks some uppercase accented letters ("Ã"),
        # so mostly-uppercase text is translated in lowercase and uppercased back.
        all_caps = [_mostly_uppercase(s) for s in sentences]
        prepared = [s.lower() if caps else s for s, caps in zip(sentences, all_caps)]
        protected = [protect(s, self._unknown_words(s), self.glossary) for s in prepared]
        decoded = self._run(protected)

        translations = []
        for text, caps, source in zip(decoded, all_caps, sentences):
            if UNKNOWN in text:
                log.warning("Untranslatable characters in %r; keeping the original", source[:40])
                translations.append(source)
            else:
                translations.append(text.upper() if caps else text)
        return translations

    def _run(self, protected: list[tuple[str, list[str]]]) -> list[str]:
        tokens = [
            [self.target_token, *self.source_sp.encode(text, out_type=str), "</s>"]
            for text, _ in protected
        ]
        # disable_unk: the target vocabulary lacks a few characters (e.g. "À"),
        # so make the model pick another wording instead of emitting "⁇".
        results = self.model.translate_batch(
            tokens, beam_size=4, max_batch_size=BATCH_SIZE, disable_unk=True
        )
        return [
            restore(self.target_sp.decode(result.hypotheses[0]), originals)
            for result, (_, originals) in zip(results, protected)
        ]

    def _unknown_words(self, text: str) -> re.Pattern | None:
        """Words holding characters the model can't read ("VistaLINK®", "<Enter>").

        They are kept as-is instead of being translated into garbage.
        """
        unknown = {c for c in set(text) if not c.isspace() and self._is_unknown(c)}
        if not unknown:
            return None
        chars = "".join(re.escape(c) for c in unknown)
        return re.compile(rf"[^\s{chars}]*[{chars}][^\s]*")

    def _is_unknown(self, char: str) -> bool:
        if char not in self._unknown_cache:
            ids = self.source_sp.encode(char)
            self._unknown_cache[char] = self.source_sp.unk_id() in ids
        return self._unknown_cache[char]


def _mostly_uppercase(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) > 1 and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


def _preload_cuda_libraries() -> None:
    """Load cuBLAS from the `nvidia-cublas-cu12` wheel (the `gpu` extra).

    pip-installed CUDA libraries are not on the dynamic linker path, so
    CTranslate2 cannot find them unless they are loaded beforehand.
    """
    try:
        import nvidia.cublas
    except ImportError:
        return  # rely on a system-wide CUDA installation
    lib_dir = Path(nvidia.cublas.__path__[0]) / "lib"
    for name in ("libcublasLt.so.12", "libcublas.so.12"):
        if (lib_dir / name).exists():
            ctypes.CDLL(str(lib_dir / name), mode=ctypes.RTLD_GLOBAL)
