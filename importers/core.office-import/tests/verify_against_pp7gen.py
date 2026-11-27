"""Check the Python port against pp7gen's frozen expected.json fixtures.

The fixtures stay in the pp7gen repo (they are WorshipKit's documents); pass
its path. For every .docx/.pages fixture with an expected.json, the port's
paragraph segments are flattened into a per-character stream of
(char, formatting), and every formatted run in expected.json must appear in
that stream with identical formatting. Content strategies only remove text
(references, quote marks, boundary whitespace) or join paragraphs with "\\n",
so containment is the right test for the reader layer.

Usage: python3 tests/verify_against_pp7gen.py /path/to/pp7gen [--verbose]
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "adapters"))
import docx_reader  # noqa: E402
import pages_reader  # noqa: E402
from segments import ATTRIBUTES  # noqa: E402

PP7GEN = pathlib.Path(sys.argv[1])
VERBOSE = "--verbose" in sys.argv
SEP = "\u0000"  # paragraph boundary: runs may not match across it


def attrs_of(seg):
    return tuple(seg.get(a) for a in ATTRIBUTES)


def stream(paragraphs):
    chars, attrs, images = [], [], []
    for p in paragraphs:
        if p.get("image"):
            images.append(pathlib.Path(p["image"]).name)
            continue
        for seg in p.get("segments", []):
            for c in seg["text"]:
                chars.append(c)
                attrs.append(attrs_of(seg))
        chars.append(SEP)
        attrs.append(None)
    return "".join(chars), attrs, images


def find_with_attrs(text, attrs, piece, want):
    start = text.find(piece)
    while start != -1:
        if all(attrs[start + i] == want for i in range(len(piece))):
            return True
        start = text.find(piece, start + 1)
    return False


def expected_runs(expected):
    slides = expected["slides"] if isinstance(expected, dict) else expected
    for slide in slides:
        if slide.get("image"):
            yield ("image", pathlib.Path(slide["image"]).name, None)
            continue
        for seg in slide.get("segments", []):
            for piece in seg["text"].split("\n"):
                if piece.strip():
                    yield ("text", piece, attrs_of(seg))


def check_fixture(path, expected_path, faithful):
    temp = []
    if path.suffix == ".pages":
        paragraphs = pages_reader.parse_paragraphs(str(path), temp)
    else:
        paragraphs = docx_reader.parse_paragraphs(str(path), temp, faithful=faithful)
    text, attrs, images = stream(paragraphs)
    expected = json.loads(expected_path.read_text())
    total = matched = 0
    text_only = 0
    misses = []
    exp_images = 0
    for kind, piece, want in expected_runs(expected):
        if kind == "image":
            exp_images += 1
            continue
        total += len(piece)
        if find_with_attrs(text, attrs, piece, want):
            matched += len(piece)
        else:
            if piece in text:
                text_only += len(piece)
            misses.append((piece, want))
    return dict(total=total, matched=matched, text_only=text_only, misses=misses,
                images=len(images), expected_images=exp_images)


def main():
    fixtures = sorted(p for p in PP7GEN.joinpath("test/fixtures").rglob("input.*")
                      if p.suffix in (".docx", ".pages") and p.with_name("expected.json").exists())
    print(f"{len(fixtures)} fixtures with expected.json\n")
    print(f"{'fixture':58} {'chars':>7} {'exact':>7} {'text ok':>7} {'imgs':>9}")
    grand = grand_ok = 0
    results = {}
    for f in fixtures:
        r = check_fixture(f, f.with_name("expected.json"), faithful=True)
        results[f] = r
        grand += r["total"]
        grand_ok += r["matched"]
        pct = 100.0 * r["matched"] / r["total"] if r["total"] else 100.0
        name = str(f.relative_to(PP7GEN / "test/fixtures"))
        print(f"{name:58} {r['total']:7} {pct:6.1f}% {r['text_only']:7} {r['images']:4}/{r['expected_images']:<4}")
        if VERBOSE:
            for piece, want in r["misses"][:6]:
                print(f"      miss: {piece[:70]!r} {dict(zip(ATTRIBUTES, want))}")
    print(f"\noverall: {grand_ok}/{grand} characters reproduced with identical formatting "
          f"({100.0 * grand_ok / grand:.2f}%)")
    return results


if __name__ == "__main__":
    main()
