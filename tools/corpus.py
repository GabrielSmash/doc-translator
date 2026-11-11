"""Public documents used to measure the pipeline on real layouts.

They are downloaded on demand and checked by SHA-256, never stored in the
repository: a document that changes would silently change every number
measured on it, so a hash mismatch is an error.
"""

import hashlib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CACHE = Path.home() / ".cache" / "doc-translator" / "corpus"
USER_AGENT = "doc-translator-measure/0.1 (+https://github.com/GabrielSmash/doc-translator)"


@dataclass(frozen=True)
class Document:
    name: str
    url: str
    sha256: str
    pages: tuple[int, int]  # first and last page measured, 0-based
    why: str


CORPUS = [
    Document(
        name="arxiv",
        url="https://arxiv.org/pdf/1706.03762v7",
        sha256="bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
        pages=(0, 5),
        why="two-column LaTeX paper: Type 1 fonts, math, dense justified text",
    ),
    Document(
        name="irs-w4",
        # The archived 2025 form: /pub/irs-pdf/fw4.pdf changes every year.
        url="https://www.irs.gov/pub/irs-prior/fw4--2025.pdf",
        sha256="94fa236f26c1a6dbbdeb40b8bd8870793e7a5be0f74ead3c2799d2b0961be68a",
        pages=(0, 3),
        why="dense form: hundreds of short labels, rules and boxes",
    ),
    Document(
        name="nist",
        url="https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-207.pdf",
        sha256="0290d6ece24874287316f4bf430fef770aa4ec08a2227c8f2c1e5b2ff975e03d",
        pages=(9, 16),
        why="Word report: subset fonts, internal links, rotated margin text",
    ),
]


class CorpusError(RuntimeError):
    pass


def fetch(document: Document, download: bool = True) -> Path:
    """Path to the verified document, downloading it if allowed."""
    path = CACHE / f"{document.name}.pdf"
    if not path.exists():
        if not download:
            raise CorpusError(f"{document.name} is not in {CACHE}; run with downloads allowed")
        CACHE.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(document.url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        _check(document, data)
        path.write_bytes(data)
    else:
        _check(document, path.read_bytes())
    return path


def _check(document: Document, data: bytes) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if digest != document.sha256:
        raise CorpusError(
            f"{document.name}: SHA-256 {digest} does not match the pinned {document.sha256}; "
            "the document changed upstream, so measurements would not be comparable"
        )
