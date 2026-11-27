# Ported from pp7gen (https://github.com/WorshipKit/pp7gen), Apache-2.0,
# Copyright WorshipKit. Source: lib/pp7gen/strategies/docx_input_strategy.rb
# (parse_paragraphs and its helpers). pp7gen reads paragraphs through the
# `docx` gem 0.10.0; the gem's exact semantics are reproduced here:
#   - paragraphs: direct w:p children of w:body only (tables are skipped)
#   - runs: direct w:r and w:hyperlink children of the paragraph
#   - run text: w:t children plus w:r/w:t grandchildren (tabs, breaks dropped)
#   - bold/italic/underline: any descendant w:b / w:i / w:u, whatever its w:val
"""Read a .docx document into styled paragraph records:

    {"text": str, "segments": [...], "style": "title"|"heading"|None, "level": int,
     "page_break": bool}                                 text paragraph
    {"image": path, "reference": None}                   image paragraph

`faithful=True` reproduces pp7gen exactly. The default adds three improvements
that never change existing segment formatting: soft line breaks (Shift-Return)
become "\\n", tabs become a space, and heading/title styles and page breaks are
reported for slide splitting.
"""
import os
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from segments import HighlightColor, TextColor, build, ruby_strip, same_attributes

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class NotADocx(Exception):
    pass


def parse_paragraphs(file_path, temp_dirs, faithful=False):
    try:
        z = zipfile.ZipFile(file_path)
    except zipfile.BadZipFile as e:
        raise NotADocx(str(e))
    with z:
        names = set(z.namelist())
        if "word/document.xml" not in names:
            raise NotADocx("no word/document.xml")

        # Image relationships (Target containing 'media/'), extracted to a temp dir.
        rid_to_image, image_temp_paths = {}, {}
        if "word/_rels/document.xml.rels" in names:
            rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
            for rel in rels.iter():
                if _local(rel.tag) == "Relationship":
                    target = rel.get("Target")
                    if target and "media/" in target:
                        rid_to_image[rel.get("Id")] = target
        if rid_to_image:
            temp_dir = tempfile.mkdtemp(prefix="docx_images")
            temp_dirs.append(temp_dir)
            for rid, target in rid_to_image.items():
                zip_path = f"word/{target}"
                if zip_path not in names:
                    continue
                dest = os.path.join(temp_dir, os.path.basename(target))
                with open(dest, "wb") as f:
                    f.write(z.read(zip_path))
                image_temp_paths[rid] = dest

        root = ET.fromstring(z.read("word/document.xml"))
        style_names = _style_names(z, names)

    # Underline strategy: if any <w:u> has both a real w:val and a w:color, a
    # run is underlined only when its own rPr/w:u has a w:val. Otherwise any
    # <w:u> counts (the docx gem's behavior).
    strict_underline = any(
        u.get(W + "val") not in (None, "none") and u.get(W + "color") is not None
        for u in root.iter(W + "u"))

    paragraphs = []
    body = root.find(W + "body")
    if body is None:
        return paragraphs
    for p in body.findall(W + "p"):
        drawings = list(p.iter(W + "drawing"))
        p_text = paragraph_text(p, faithful)
        if drawings:
            for drawing in drawings:
                blip = next((el for el in drawing.iter() if _local(el.tag) == "blip"), None)
                if blip is None:
                    continue
                rid = next((v for k, v in blip.attrib.items() if _local(k) == "embed"), None)
                if rid and image_temp_paths.get(rid):
                    paragraphs.append({"image": image_temp_paths[rid], "reference": None})
            if ruby_strip(p_text) == "":
                continue

        extra = {} if faithful else _paragraph_extras(p, style_names)
        if ruby_strip(p_text) == "":
            paragraphs.append(dict({"text": "", "segments": []}, **extra))
            continue

        segments = []
        for r in text_runs(p):
            run_text = run_text_of(r, faithful)
            if run_text == "":
                continue
            rpr = r.find(W + "rPr")
            bold = _has_desc(r, "b")
            italic = _has_desc(r, "i")
            underline = docx_strict_underlined(rpr) if strict_underline else _has_desc(r, "u")
            attrs = docx_run_enrichment(rpr)
            attrs.update(bold=bold, italic=italic, underline=underline)
            candidate = build(run_text, attrs)
            if segments and same_attributes(segments[-1], candidate):
                segments[-1]["text"] += run_text
            else:
                segments.append(candidate)
        paragraphs.append(dict({"text": p_text, "segments": segments}, **extra))
    return paragraphs


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _has_desc(node, name):
    """XPath './/w:<name>' from the run node: any descendant."""
    return node.find(".//" + W + name) is not None


def text_runs(p):
    return [c for c in p if c.tag in (W + "r", W + "hyperlink")]


def run_text_of(r, faithful):
    if faithful:
        parts = [t.text or "" for t in r.findall(W + "t")]
        parts += [t.text or "" for rr in r.findall(W + "r") for t in rr.findall(W + "t")]
        return "".join(parts)
    # Document order, with soft breaks as "\n" and tabs as a space.
    runs = [r] if r.tag == W + "r" else r.findall(W + "r")
    out = []
    for run in runs:
        for child in run:
            if child.tag == W + "t":
                out.append(child.text or "")
            elif child.tag == W + "tab":
                out.append(" ")
            elif child.tag in (W + "br", W + "cr") and child.get(W + "type") in (None, "textWrapping"):
                out.append("\n")
    return "".join(out)


def paragraph_text(p, faithful):
    return "".join(run_text_of(r, faithful) for r in text_runs(p))


def docx_strict_underlined(rpr):
    if rpr is None:
        return False
    u = rpr.find(W + "u")
    if u is None:
        return False
    val = u.get(W + "val")
    return val is not None and val != "none"


def docx_run_enrichment(rpr):
    if rpr is None:
        return {"highlight": None, "strike": False, "font_size": None,
                "text_color": None, "text_color_hex": None}
    hl = rpr.find(W + "highlight")
    highlight = HighlightColor.from_docx_name(hl.get(W + "val") if hl is not None else None)
    st = rpr.find(W + "strike")
    strike = False if st is None else str(st.get(W + "val") or "") not in ("0", "false", "off")
    sz = rpr.find(W + "sz")
    raw = sz.get(W + "val") if sz is not None else None
    font_size = None if raw is None else _ruby_to_f(raw) / 2.0  # half-points
    col = rpr.find(W + "color")
    color_val = col.get(W + "val") if col is not None else None
    text_color = TextColor.from_docx_value(color_val)
    text_color_hex = None if text_color is None else docx_color_hex(color_val)
    return {"highlight": highlight, "strike": strike, "font_size": font_size,
            "text_color": text_color, "text_color_hex": text_color_hex}


def _ruby_to_f(s):
    """Ruby String#to_f: leading numeric prefix, 0.0 if none."""
    m = re.match(r"\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?", s)
    return float(m.group(0)) if m else 0.0


def docx_color_hex(value):
    if value is None:
        return None
    digits = re.sub(r"\A#", "", str(value))
    if not digits or digits.lower() == "auto" or not re.fullmatch(r"[0-9a-fA-F]{6}", digits):
        return None
    return "#" + digits.lower()


# --- additions beyond pp7gen (default mode only) -----------------------------

def _style_names(z, names):
    if "word/styles.xml" not in names:
        return {}
    root = ET.fromstring(z.read("word/styles.xml"))
    out = {}
    for s in root.iter(W + "style"):
        sid, name = s.get(W + "styleId"), s.find(W + "name")
        if sid and name is not None:
            out[sid] = (name.get(W + "val") or "").lower()
    return out


def _paragraph_extras(p, style_names):
    """Heading/title style (matched by style *name*, so localized ids work)
    and whether the paragraph contains an explicit page break."""
    ppr = p.find(W + "pPr")
    sid = ""
    if ppr is not None and ppr.find(W + "pStyle") is not None:
        sid = ppr.find(W + "pStyle").get(W + "val") or ""
    name = style_names.get(sid, sid.lower())
    style, level = None, 0
    if name == "title":
        style = "title"
    else:
        m = re.match(r"heading\s*(\d)", name)
        if m:
            style, level = "heading", int(m.group(1))
    page_break = any(br.get(W + "type") == "page" for br in p.iter(W + "br")) or (
        ppr is not None and ppr.find(W + "pageBreakBefore") is not None)
    return {"style": style, "level": level, "page_break": page_break}
