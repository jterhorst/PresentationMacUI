"""Tests for the first-party .docx and .pages importers.

Self-contained checks build their own inputs: Snappy vectors, a synthetic
.pages file (real IWA framing and iWork messages), and generated .docx files.
If PP7GEN points at a pp7gen checkout, the adapters also run end to end on
its real fixtures.

Usage: ENGINE_DIR=/path/to/PresentationEngine python3 tests/test_office_import.py <generated-python-dir>
(or run scripts/test_importers.sh from the repo root)
"""
import json
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import zipfile

GEN = pathlib.Path(sys.argv[1]).resolve()
HERE = pathlib.Path(__file__).resolve().parent
PKG = HERE.parent
ADAPTERS = PKG / "adapters"
sys.path[:0] = [str(GEN), str(ADAPTERS)]

from google.protobuf.struct_pb2 import Struct  # noqa: E402
from importer.v1 import importer_pb2 as pb  # noqa: E402
import iwork as iw  # noqa: E402
import pages_reader  # noqa: E402

failures = 0


def check(name, ok, detail=""):
    global failures
    print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail and not ok else ""))
    failures += 0 if ok else 1


# --- Snappy and IWA framing ---------------------------------------------------

check("snappy: literal-only block", iw.snappy_decompress(b"\x05\x10hello") == b"hello")
check("snappy: overlapping 1-byte-offset copy repeats the pattern",
      iw.snappy_decompress(b"\x09\x08abc\x09\x03") == b"abcabcabc")
check("snappy: 2-byte-offset copy",
      iw.snappy_decompress(b"\x08\x0cabcd" + bytes([(4 - 1) << 2 | 2]) + (4).to_bytes(2, "little")) == b"abcdabcd")
long = bytes(range(100))
check("snappy: literal with an extended length byte",
      iw.snappy_decompress(b"\x64" + bytes([60 << 2]) + bytes([99]) + long) == long)
check("snappy: malformed input returns None, not an exception", iw.snappy_decompress(b"\x05\x10he") in (None, b"he"))


def snappy_literals(data):
    """Tiny compressor: varint length + 60-byte literal chunks."""
    out = bytearray()
    n = len(data)
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            break
    for i in range(0, len(data), 60):
        chunk = data[i:i + 60]
        out.append((len(chunk) - 1) << 2)
        out += chunk
    return bytes(out)


def iwa(objects):
    """objects: [(identifier, [(type, message)])] -> one IWA file (Snappy-framed)."""
    stream = bytearray()
    for ident, msgs in objects:
        info = iw.ArchiveInfo(identifier=ident)
        payloads = []
        for mtype, msg in msgs:
            data = msg.SerializePartialToString()
            info.message_infos.add(type=mtype, length=len(data))
            payloads.append(data)
        head = info.SerializePartialToString()
        n, varint = len(head), bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            varint.append(b | (0x80 if n else 0))
            if not n:
                break
        stream += varint + head + b"".join(payloads)
    body = snappy_literals(bytes(stream))
    return bytes([0]) + len(body).to_bytes(3, "little") + body


# --- Synthetic .pages ----------------------------------------------------------

TEXT = "Verse 1\nThe Lord is my shepherd\nI shall not want\n\nChorus\nGoodness and mercy\n\nCCLI 1234"
lord = TEXT.index("The Lord")
shep = TEXT.index("shepherd")
storage = iw.StorageArchive(kind=iw.BODY)
storage.text.append(TEXT)
for ci, ref in ((0, None), (lord, 10), (lord + len("The Lord"), None), (shep, 11), (shep + 8, None)):
    e = storage.table_char_style.entries.add(character_index=ci)
    if ref:
        e.object.identifier = ref
storage.table_para_style.entries.add(character_index=0).object.identifier = 20
bold_style = iw.CharacterStyleArchive()
bold_style.char_properties.bold = True
hl_style = iw.CharacterStyleArchive()
c = hl_style.char_properties.background_color
c.r, c.g, c.b, c.a = 1.0, 1.0, 0.0, 1.0
para_style = iw.ParagraphStyleArchive()
para_style.char_properties.font_size = 24.0

tmp = pathlib.Path(tempfile.mkdtemp())
pages_file = tmp / "Psalm 23.pages"
with zipfile.ZipFile(pages_file, "w") as z:
    z.writestr("Index/Document.iwa", iwa([(1, [(2001, storage)]), (10, [(2021, bold_style)]),
                                          (11, [(2021, hl_style)]), (20, [(2022, para_style)])]))
    z.writestr("Metadata/Properties.plist", b"")

paras = pages_reader.parse_paragraphs(str(pages_file), [])
check("synthetic .pages: BODY storage found and split into paragraphs",
      [p["text"] for p in paras] == TEXT.split("\n"), str([p["text"] for p in paras]))
segs = paras[1]["segments"]
check("explicit bold char style applies to its run only",
      [(s["text"], s["bold"]) for s in segs][:2] == [("The Lord", True), (" is my ", False)], str(segs))
check("background color snaps to a highlight name", any(s["text"] == "shepherd" and s["highlight"] == "yellow"
                                                         for s in segs), str(segs))
check("paragraph style font size reaches segments", all(s["font_size"] == 24.0 for s in segs))


# --- Adapter host ----------------------------------------------------------------

def run(adapter, path, original, options=None):
    env = dict(os.environ, PYTHONPATH=f"{GEN}{os.pathsep}{ADAPTERS}")
    proc = subprocess.Popen([sys.executable, str(ADAPTERS / adapter)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    def send(req):
        data = req.SerializeToString()
        proc.stdin.write(struct.pack(">I", len(data)) + data)
        proc.stdin.flush()

    def recv():
        h = proc.stdout.read(4)
        if len(h) < 4:
            return None
        r = pb.AdapterResponse()
        r.ParseFromString(proc.stdout.read(struct.unpack(">I", h)[0]))
        return r

    send(pb.AdapterRequest(describe=pb.Describe(protocol_version=1)))
    info = recv().info
    out = tempfile.mkdtemp()
    req = pb.AdapterRequest()
    req.import_request.input_path = str(path)
    req.import_request.original_filename = original
    req.import_request.output_dir = out
    o = Struct()
    o.update(options or {})
    req.import_request.options.CopyFrom(o)
    send(req)
    final = None
    while True:
        r = recv()
        if r is None or r.WhichOneof("body") != "progress":
            final = r
            break
    proc.stdin.close()
    proc.wait(timeout=60)
    return info, final, out, proc.stderr.read().decode()


info, res, out, err = run("pages.py", pages_file, "Psalm 23.pages")
check("pages adapter describes itself", info.adapter_id == "core.office-import/pages" and "pages" in info.file_types[0].extensions)
docs = res.result.documents if res and res.WhichOneof("body") == "result" else []
check("pages adapter: labeled slides and CCLI metadata",
      len(docs) == 1 and [s.semantic.label for s in docs[0].slides] == ["Verse 1", "Chorus"]
      and docs[0].metadata.get("ccli") == "CCLI 1234" and docs[0].name == "Psalm 23", err or str(docs))
if docs:
    rtf = docs[0].slides[0].semantic.blocks[0].rtf
    check("bold and highlight survive into RTF", b"{\\b The Lord}" in rtf and b"\\highlight1 shepherd" in rtf
          and b"\\colortbl;\\red255\\green255\\blue0;" in rtf, rtf.decode())

# Folder-package .pages
pkg = tmp / "Package.pages"
(pkg / "Index").mkdir(parents=True)
with zipfile.ZipFile(pages_file) as z:
    (pkg / "Index" / "Document.iwa").write_bytes(z.read("Index/Document.iwa"))
_, res, _, err = run("pages.py", pkg, "Package.pages")
check("folder-package .pages imports the same content",
      res.WhichOneof("body") == "result" and len(res.result.documents[0].slides) == 2, err)

legacy = tmp / "Old.pages"
with zipfile.ZipFile(legacy, "w") as z:
    z.writestr("index.xml.gz", b"\x1f\x8b")
_, res, _, _ = run("pages.py", legacy, "Old.pages")
check("Pages '09 files get a clear UNSUPPORTED_FORMAT message",
      res.WhichOneof("body") == "error" and res.error.code == pb.AdapterError.CODE_UNSUPPORTED_FORMAT
      and "older format" in res.error.message)


# --- Generated .docx ---------------------------------------------------------------

def make_docx(path, body_xml, styles_xml=None, rels=None, media=None):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    doc = (f'<?xml version="1.0"?><w:document xmlns:w="{w}" xmlns:r="{r}" '
           f'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>{body_xml}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
        if styles_xml:
            z.writestr("word/styles.xml", f'<w:styles xmlns:w="{w}">{styles_xml}</w:styles>')
        if rels:
            z.writestr("word/_rels/document.xml.rels",
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       + rels + "</Relationships>")
        for name, data in (media or {}).items():
            z.writestr(name, data)


def p(*runs, style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def r(text, props=""):
    return f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
docx_file = tmp / "Sunday.docx"
make_docx(docx_file, "".join([
    p(r("How Great Thou Art"), style="Title"),
    p(r("Verse 1"), style="Heading2"),
    p(r("O Lord my God"), '<w:r><w:br/></w:r>', r("When I in "), r("awesome", "<w:b/><w:color w:val=\"C83232\"/>"),
      r(" wonder")),
    p(),
    p(r("Struck", "<w:strike/>"), r(" and "), r("marked", '<w:highlight w:val="yellow"/>')),
    p('<w:r><w:drawing><a:blip r:embed="rId5"/></w:drawing></w:r>'),
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>table text</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
]), styles_xml='<w:style w:styleId="Title"><w:name w:val="Title"/></w:style>'
               '<w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>',
    rels='<Relationship Id="rId5" Target="media/image1.png" Type="image"/>',
    media={"word/media/image1.png": PNG})

info, res, out, err = run("docx.py", docx_file, "Sunday.docx")
docs = res.result.documents if res and res.WhichOneof("body") == "result" else []
check("docx adapter: Title names the document, heading labels the slide",
      len(docs) == 1 and docs[0].name == "How Great Thou Art" and docs[0].slides[0].semantic.label == "Verse 1",
      err or str(docs))
if docs:
    s0 = docs[0].slides[0].semantic.blocks[0]
    check("soft line break splits the stanza into lines", s0.plain_text == "O Lord my God\nWhen I in awesome wonder")
    check("accent color keeps its exact source hex in RTF",
          b"\\red200\\green50\\blue50" in s0.rtf and b"{\\b\\cf1 awesome}" in s0.rtf, s0.rtf.decode())
    s1 = docs[0].slides[1].semantic.blocks[0]
    check("strike and highlight survive into RTF", b"{\\strike Struck}" in s1.rtf and b"\\highlight1 marked" in s1.rtf,
          s1.rtf.decode())
    img = docs[0].slides[2].semantic
    media = {m.local_id: m for m in res.result.media}
    check("embedded image becomes an image slide with its media copied out",
          img.media_local_id in media and media[img.media_local_id].mime_type == "image/png"
          and (pathlib.Path(out) / media[img.media_local_id].relative_path).read_bytes() == PNG)
    check("tables are skipped with a warning (pp7gen behavior)",
          any("Tables" in w.message for w in res.result.warnings)
          and all("table text" not in b.plain_text for s in docs[0].slides for b in s.semantic.blocks))
_, res, _, _ = run("docx.py", docx_file, "Sunday.docx", {"keep_formatting": False})
check("keep_formatting=false writes plain RTF",
      b"\\b" not in res.result.documents[0].slides[0].semantic.blocks[0].rtf)

bad = tmp / "bad.docx"
bad.write_bytes(b"not a zip")
_, res, _, _ = run("docx.py", bad, "bad.docx")
check("corrupt .docx -> CORRUPT_FILE", res.WhichOneof("body") == "error"
      and res.error.code == pb.AdapterError.CODE_CORRUPT_FILE)
enc = tmp / "locked.docx"
enc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
_, res, _, _ = run("docx.py", enc, "locked.docx")
check("password-protected .docx -> PASSWORD_PROTECTED",
      res.WhichOneof("body") == "error" and res.error.code == pb.AdapterError.CODE_PASSWORD_PROTECTED)


# --- Manifest -----------------------------------------------------------------------
from jsonschema import Draft202012Validator  # noqa: E402

# JSON Schemas come from the PresentationEngine contracts checkout.
ROOT = pathlib.Path(os.environ.get("ENGINE_DIR", PKG.parent.parent.parent / "PresentationEngine")).resolve()
manifest = json.loads((PKG / "plugin.json").read_text())
errs = list(Draft202012Validator(json.loads((ROOT / "schemas/logic-plugin.schema.json").read_text()))
            .iter_errors(manifest))
check("first-party manifest validates", not errs, "; ".join(e.message for e in errs[:2]))
ui_schema = json.loads((ROOT / "schemas/ui-schema.schema.json").read_text())
for imp in manifest["importers"]:
    form = json.loads((PKG / imp["options"]).read_text())
    check(f"options form '{imp['id']}' validates", not list(Draft202012Validator(ui_schema).iter_errors(form)))


# --- Real fixtures (optional) ----------------------------------------------------------
pp7gen = os.environ.get("PP7GEN")
if pp7gen:
    fixtures = sorted(f for f in pathlib.Path(pp7gen, "test/fixtures").rglob("*")
                      if f.suffix in (".docx", ".pages") and f.is_file())
    bad = []
    total_slides = 0
    for f in fixtures:
        _, res, _, err = run("pages.py" if f.suffix == ".pages" else "docx.py", f, f.name)
        if res is None or res.WhichOneof("body") != "result":
            bad.append(f"{f.name}: {res.error.message if res else err[:200]}")
            continue
        total_slides += sum(len(d.slides) for d in res.result.documents)
    check(f"all {len(fixtures)} pp7gen .docx/.pages fixtures import end to end ({total_slides} slides)",
          not bad, "; ".join(bad))
else:
    print("SKIP real-fixture run (set PP7GEN to a pp7gen checkout)")

print("ALL PASSED" if failures == 0 else f"{failures} FAILED")
sys.exit(1 if failures else 0)
