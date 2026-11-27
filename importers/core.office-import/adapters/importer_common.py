"""Shared importer plumbing: stdio framing, paragraph records -> semantic
slides (lyrics-aware), and an RTF writer for the supported subset.

The readers (docx_reader, pages_reader) emit pp7gen-style paragraph records.
This module turns them into importer.v1 ImportResult messages.
"""
import mimetypes
import os
import re
import shutil
import struct
import sys

from google.protobuf.json_format import MessageToDict
from importer.v1 import importer_pb2 as pb
from segments import HighlightColor, ruby_strip

PROTOCOL_VERSION = 1

# --- framing -------------------------------------------------------------------

def _read(stream=None):
    stream = stream or sys.stdin.buffer
    header = stream.read(4)
    if len(header) < 4:
        return None
    msg = pb.AdapterRequest()
    msg.ParseFromString(stream.read(struct.unpack(">I", header)[0]))
    return msg


def send(response):
    data = response.SerializeToString()
    sys.stdout.buffer.write(struct.pack(">I", len(data)) + data)
    sys.stdout.buffer.flush()


def progress(fraction, message):
    send(pb.AdapterResponse(progress=pb.ImportProgress(fraction=fraction, message=message)))


class AdapterFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def serve(info, do_import):
    while True:
        req = _read()
        if req is None:
            return
        kind = req.WhichOneof("body")
        if kind == "describe":
            send(pb.AdapterResponse(info=info))
        elif kind == "import_request":
            try:
                send(pb.AdapterResponse(result=do_import(req.import_request,
                                                         MessageToDict(req.import_request.options))))
            except AdapterFailure as f:
                send(pb.AdapterResponse(error=pb.AdapterError(code=f.code, message=f.message)))
            except Exception as e:  # report, never die silently
                print(f"internal error: {e!r}", file=sys.stderr)
                send(pb.AdapterResponse(error=pb.AdapterError(
                    code=pb.AdapterError.CODE_INTERNAL, message="The importer hit an unexpected error.")))
            return


# --- paragraph records -> items ------------------------------------------------

BLANK, PAGE_BREAK = "blank", "page_break"


class Run:
    __slots__ = ("text", "bold", "italic", "underline", "strike", "color", "highlight")

    def __init__(self, text, bold=False, italic=False, underline=False, strike=False,
                 color=None, highlight=None):
        self.text, self.bold, self.italic, self.underline = text, bold, italic, underline
        self.strike, self.color, self.highlight = strike, color, highlight


class Heading:
    def __init__(self, level, text):
        self.level, self.text = level, text


class Title:
    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, path):
        self.path = path


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def segment_run(seg, text):
    """pp7gen segment -> Run. Accent text colors keep their exact source hex;
    highlight names map to their reference RGB."""
    color = _hex_to_rgb(seg["text_color_hex"]) if seg.get("text_color_hex") else None
    hl = HighlightColor.REFERENCE_RGB.get(seg.get("highlight")) if seg.get("highlight") else None
    return Run(text, seg.get("bold", False), seg.get("italic", False), seg.get("underline", False),
               seg.get("strike", False), color, hl)


def paragraphs_to_items(paragraphs):
    """Paragraph records -> item stream. Segments split into lines at "\\n"."""
    items = []
    for p in paragraphs:
        if p.get("image"):
            items.append(Image(p["image"]))
            continue
        if p.get("page_break"):
            items.append(PAGE_BREAK)
        text = p.get("text", "")
        if ruby_strip(text) == "":
            items.append(BLANK)
            continue
        if p.get("style") == "title":
            items.append(Title(ruby_strip(text)))
            continue
        if p.get("style") == "heading":
            items.append(Heading(p.get("level", 1), ruby_strip(text)))
            continue
        line = []
        for seg in p.get("segments", []):
            parts = seg["text"].split("\n")
            for i, part in enumerate(parts):
                if i:
                    items.append(line)
                    line = []
                if part:
                    line.append(segment_run(seg, part))
        items.append(line)
    return items


# --- items -> documents (lyrics-aware) -----------------------------------------

SECTION_RE = re.compile(
    r"^\[?\s*(verse|chorus|pre-?chorus|bridge|tag|intro|outro|ending|refrain|interlude|vamp)"
    r"(\s*\d+)?\s*\]?\s*:?\s*$", re.IGNORECASE)
FOOTER_RE = re.compile(r"^\s*(ccli|©|\(c\)|copyright)", re.IGNORECASE)


def line_text(line):
    return "".join(r.text for r in line)


def section_label(text):
    m = SECTION_RE.match(text)
    if not m:
        return text
    raw = m.group(1).lower()
    name = "Pre-Chorus" if raw in ("prechorus", "pre-chorus") else raw.title()
    num = (m.group(2) or "").strip()
    return f"{name} {num}" if num else name


def build_result(items, options, fallback_name, output_dir, warnings):
    split_on = options.get("split_on", "none")
    lines_per_slide = int(options.get("lines_per_slide", 0) or 0)
    title_slide = bool(options.get("title_slide", False))
    keep_formatting = bool(options.get("keep_formatting", True))

    result = pb.ImportResult()
    docs, media_ids = [], {}
    state = {"doc": None, "label": ""}
    stanza = []

    def new_doc(name):
        doc = pb.ImportedDocument(name=name or fallback_name)
        docs.append(doc)
        state["doc"], state["label"] = doc, ""
        if title_slide and name:
            s = doc.slides.add().semantic
            s.label = "Title"
            add_block(s, [[Run(name)]], pb.TEXT_ROLE_TITLE, keep_formatting)

    def ensure_doc():
        if state["doc"] is None:
            label = state["label"]  # a label seen before the first slide still applies
            new_doc(fallback_name)
            state["label"] = label
        return state["doc"]

    def flush():
        nonlocal stanza
        if not stanza:
            return
        doc = ensure_doc()
        chunks = [stanza] if lines_per_slide <= 0 else [
            stanza[i:i + lines_per_slide] for i in range(0, len(stanza), lines_per_slide)]
        for n, chunk in enumerate(chunks):
            s = doc.slides.add().semantic
            base = state["label"]
            s.label = base if n == 0 or not base else f"{base} ({n + 1})"
            s.group = base
            add_block(s, chunk, pb.TEXT_ROLE_BODY, keep_formatting)
        stanza = []

    for item in items:
        if item == BLANK:
            flush()
        elif item == PAGE_BREAK:
            flush()
            if split_on == "page_break" and state["doc"] is not None and len(state["doc"].slides):
                state["doc"] = None
        elif isinstance(item, Image):
            flush()
            local_id = media_ids.get(item.path)
            if local_id is None:
                local_id = f"m{len(media_ids) + 1}"
                media_ids[item.path] = local_id
                rel = f"{local_id}_{os.path.basename(item.path)}"
                shutil.copyfile(item.path, os.path.join(output_dir, rel))
                result.media.add(local_id=local_id, relative_path=rel,
                                 mime_type=mimetypes.guess_type(item.path)[0] or "application/octet-stream")
            s = ensure_doc().slides.add().semantic
            s.label = "Image"
            s.media_local_id = local_id
        elif isinstance(item, Title):
            flush()
            if state["doc"] is None or not len(state["doc"].slides) or split_on != "none":
                new_doc(item.text)
        elif isinstance(item, Heading):
            flush()
            if item.level == 1 and split_on == "heading1":
                new_doc(item.text)
            elif item.text:
                state["label"] = section_label(item.text)
        else:
            text = ruby_strip(line_text(item))
            if not text:
                flush()
            elif FOOTER_RE.match(text):
                flush()
                ensure_doc().metadata["ccli" if text.lower().startswith("ccli") else "copyright"] = text
            elif SECTION_RE.match(text):
                flush()
                state["label"] = section_label(text)
            else:
                stanza.append(item)
    flush()

    def has_content(d):
        return any(s.semantic.media_local_id or any(b.role != pb.TEXT_ROLE_TITLE for b in s.semantic.blocks)
                   for s in d.slides)

    docs = [d for d in docs if has_content(d)]
    if not docs:
        warnings.append(pb.ImportWarning(severity=pb.ImportWarning.SEVERITY_WARNING, document_index=-1,
                                         message="No text or images found to make slides from."))
    result.documents.extend(docs)
    result.warnings.extend(warnings)
    return result


def add_block(slide, lines, role, keep_formatting):
    block = slide.blocks.add(role=role)
    block.plain_text = "\n".join(ruby_strip(line_text(l)) for l in lines)
    block.rtf = to_rtf(lines, keep_formatting)


# --- RTF writer (supported subset) -------------------------------------------

def _escape(text):
    out = []
    for ch in text:
        code = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\t":
            out.append("\\tab ")
        elif code < 128:
            out.append(ch)
        elif code > 0xFFFF:  # UTF-16 surrogate pair
            v = code - 0x10000
            for unit in (0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF)):
                out.append(f"\\u{unit - 65536}?")
        else:
            out.append(f"\\u{code if code < 32768 else code - 65536}?")
    return "".join(out)


def to_rtf(lines, keep_formatting=True):
    colors = []
    if keep_formatting:
        for line in lines:
            for r in line:
                for c in (r.color, r.highlight):
                    if c and c not in colors:
                        colors.append(c)
    head = "{\\rtf1\\ansi\\ansicpg1252\\uc1\\deff0{\\fonttbl{\\f0 Helvetica;}}"
    if colors:
        head += "{\\colortbl;" + "".join(f"\\red{r}\\green{g}\\blue{b};" for r, g, b in colors) + "}"
    parts = [head + "\\f0 "]
    for i, line in enumerate(lines):
        if i:
            parts.append("\\par ")
        for r in line:
            if not r.text:
                continue
            codes = ""
            if keep_formatting:
                codes += "\\b" if r.bold else ""
                codes += "\\i" if r.italic else ""
                codes += "\\ul" if r.underline else ""
                codes += "\\strike" if r.strike else ""
                codes += f"\\cf{colors.index(r.color) + 1}" if r.color else ""
                codes += f"\\highlight{colors.index(r.highlight) + 1}" if r.highlight else ""
            parts.append("{" + codes + " " + _escape(r.text) + "}" if codes else _escape(r.text))
    parts.append("}")
    return "".join(parts).encode("ascii")
