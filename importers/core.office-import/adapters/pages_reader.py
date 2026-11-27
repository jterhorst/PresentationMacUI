# Ported from pp7gen (https://github.com/WorshipKit/pp7gen), Apache-2.0,
# Copyright WorshipKit. Source: lib/pp7gen/strategies/pages_input_strategy.rb
# (parse_paragraphs and its helpers). Comments from the original that explain
# Pages quirks are kept, shortened.
"""Read a .pages document into styled paragraph records:

    {"text": str, "segments": [segment, ...]}           text paragraph
    {"text": str, "segments": [], "image": path}         image paragraph
"""
import os
import re
import tempfile
import zipfile

import iwork as iw
from segments import (HighlightColor, TextColor, build, merge_punctuation_segments, ruby_round,
                      same_attributes)

IWA_RE = re.compile(r"\AIndex/.*\.iwa\Z")


def parse_paragraphs(file_path, temp_dirs):
    object_map, attachment_positions, image_temp_paths = {}, {}, {}
    temp_dir = None
    with zipfile.ZipFile(file_path) as z:
        for info in z.infolist():  # central-directory order, like Zip::File#each
            if not IWA_RE.match(info.filename):
                continue
            try:
                iw.parse_iwa_into_object_map(z.read(info), object_map)
            except Exception:
                continue  # skip IWA files that fail to parse

        storage = find_body_storage(object_map)
        if storage is None:
            return []

        data_id_to_filename = None
        if storage.HasField("table_attachment"):
            data_id_to_filename = build_data_id_to_filename_map(object_map)
            for entry in storage.table_attachment.entries:
                if not entry.HasField("object"):
                    continue
                zip_path = resolve_attachment_to_image_path(entry.object.identifier, object_map,
                                                            data_id_to_filename)
                if zip_path:
                    attachment_positions[entry.character_index] = zip_path
            needed = list(dict.fromkeys(p for p in attachment_positions.values() if p))
            if needed:
                temp_dir = tempfile.mkdtemp(prefix="pages_images")
                temp_dirs.append(temp_dir)
                for zip_path in needed:
                    if zip_path not in z.namelist():
                        continue
                    dest = os.path.join(temp_dir, os.path.basename(zip_path))
                    with open(dest, "wb") as f:
                        f.write(z.read(zip_path))
                    image_temp_paths[zip_path] = dest

        char_styles = build_char_styles(object_map)
        style_runs = build_style_runs(storage, char_styles)
        para_style_runs = build_para_style_runs(storage, object_map)
        comment_highlight_ranges = build_comment_highlight_ranges(storage)
        full_text = "".join(storage.text)

        # Floating / wrap-anchored images live in DrawableArchive -> ImageArchive
        # chains with no table_attachment entry on BODY.
        if data_id_to_filename is None:
            data_id_to_filename = build_data_id_to_filename_map(object_map)
        trailing = []
        for zip_path in floating_image_zip_paths_to_extract(object_map, data_id_to_filename,
                                                            image_temp_paths):
            if zip_path not in z.namelist():
                continue
            if temp_dir is None:
                temp_dir = tempfile.mkdtemp(prefix="pages_images")
                temp_dirs.append(temp_dir)
            dest = os.path.join(temp_dir, os.path.basename(zip_path))
            with open(dest, "wb") as f:
                f.write(z.read(zip_path))
            image_temp_paths[zip_path] = dest
            trailing.append(dest)

        paragraphs = split_into_styled_paragraphs(full_text, style_runs, attachment_positions,
                                                  image_temp_paths, para_style_runs,
                                                  comment_highlight_ranges)
        for path in trailing:
            paragraphs.append({"text": "", "segments": [], "image": path})
        return paragraphs


# --- colors ------------------------------------------------------------------

def _rgb(color):
    return (ruby_round(color.r * 255), ruby_round(color.g * 255), ruby_round(color.b * 255))


def pages_highlight_name(props):
    """Highlight is an RGB background_color with 0..1 float components."""
    if props.background_color_null or not props.HasField("background_color"):
        return None
    return HighlightColor.from_rgb(*_rgb(props.background_color))


def pages_text_color_rgb(props):
    if props.font_color_null or not props.HasField("font_color"):
        return None
    return _rgb(props.font_color)


def pages_text_color_name(props):
    rgb = pages_text_color_rgb(props)
    return None if rgb is None else TextColor.from_rgb(*rgb)


def pages_text_color_hex(props, semantic_name=None):
    """'#rrggbb' only for colors that snap to a semantic name (accent colors)."""
    semantic_name = semantic_name or pages_text_color_name(props)
    if semantic_name is None:
        return None
    rgb = pages_text_color_rgb(props)
    return None if rgb is None else "#%02x%02x%02x" % rgb


# --- object lookup -----------------------------------------------------------

def find_body_storage(object_map):
    for messages in object_map.values():
        for msg in messages:
            storage = iw.try_decode(iw.StorageArchive, msg["data"])
            if storage is not None and storage.kind == iw.BODY and len(storage.text):
                return storage
    return None


def build_char_styles(object_map):
    styles = {}
    for obj_id, messages in object_map.items():
        for msg in messages:
            style = iw.try_decode(iw.CharacterStyleArchive, msg["data"])
            if style is not None and style.HasField("char_properties"):
                styles[obj_id] = style
    return styles


def build_data_id_to_filename_map(object_map):
    mapping = {}
    for messages in object_map.values():
        for msg in messages:
            meta = iw.try_decode(iw.PackageMetadata, msg["data"])
            if meta is None or not len(meta.datas):
                continue
            for d in meta.datas:
                fname = d.file_name if d.file_name else d.preferred_file_name
                if fname:
                    mapping[d.identifier] = f"Data/{fname}"
    return mapping


def collect_image_archive_data_ids(object_map):
    ids, seen = [], set()
    for messages in object_map.values():
        for msg in messages:
            img = iw.try_decode(iw.ImageArchive, msg["data"])
            if img is None or not img.HasField("data") or img.data.identifier <= 0:
                continue
            if img.data.identifier not in seen:
                seen.add(img.data.identifier)
                ids.append(img.data.identifier)
    return ids


def floating_image_zip_paths_to_extract(object_map, data_id_to_filename, image_temp_paths):
    paths, seen = [], set()
    for data_id in collect_image_archive_data_ids(object_map):
        zip_path = data_id_to_filename.get(data_id)
        if not zip_path or zip_path in image_temp_paths or zip_path in seen:
            continue
        seen.add(zip_path)
        paths.append(zip_path)
    return paths


def resolve_attachment_to_image_path(attachment_obj_id, object_map, data_id_to_filename):
    """DrawableAttachmentArchive -> ImageArchive -> DataReference -> filename."""
    drawable_ref = None
    for msg in object_map.get(attachment_obj_id, []):
        att = iw.try_decode(iw.DrawableAttachmentArchive, msg["data"])
        if att is None:
            continue
        drawable_ref = att.drawable if att.HasField("drawable") else None
        if drawable_ref is not None:
            break
    if drawable_ref is None:
        return None
    for msg in object_map.get(drawable_ref.identifier, []):
        img = iw.try_decode(iw.ImageArchive, msg["data"])
        if img is not None and img.HasField("data") and img.data.identifier > 0:
            return data_id_to_filename.get(img.data.identifier)
    return None


# --- style runs ----------------------------------------------------------------

def _runs_bounds(entries, full_len):
    entries = sorted(entries, key=lambda e: e.character_index)
    for i, e in enumerate(entries):
        end = entries[i + 1].character_index if i + 1 < len(entries) else full_len
        yield e, e.character_index, end


def build_para_style_runs(storage, object_map):
    """Paragraph style runs. Entries without an object inherit the previous one."""
    runs = []
    if not storage.HasField("table_para_style"):
        return runs
    full_len = len("".join(storage.text))
    last = dict(bold=False, italic=False, underline=False, font_bold=False, strike=False,
                font_size=None, highlight=None, text_color=None, text_color_hex=None)
    for entry, start, end in _runs_bounds(storage.table_para_style.entries, full_len):
        if entry.HasField("object"):
            cur = dict(bold=False, italic=False, underline=False, font_bold=False, strike=False,
                       font_size=None, highlight=None, text_color=None, text_color_hex=None)
            for msg in object_map.get(entry.object.identifier, []):
                ps = iw.try_decode(iw.ParagraphStyleArchive, msg["data"])
                if ps is None:
                    continue
                if ps.HasField("char_properties"):
                    p = ps.char_properties
                    cur["bold"] = p.bold
                    cur["italic"] = p.italic
                    cur["underline"] = p.underline == iw.K_SINGLE_UNDERLINE
                    cur["font_bold"] = bool(re.search("bold", p.font_name, re.IGNORECASE))
                    cur["strike"] = p.strikethru != iw.K_NO_STRIKETHRU
                    cur["font_size"] = float(p.font_size) if p.font_size > 0 else None
                    cur["highlight"] = pages_highlight_name(p)
                    cur["text_color"] = pages_text_color_name(p)
                    cur["text_color_hex"] = pages_text_color_hex(p, cur["text_color"])
                break  # first decodable message wins, as in the Ruby
            last = cur
        else:
            cur = dict(last)
        runs.append(dict(cur, start=start, end=end, has_object=entry.HasField("object")))
    return runs


def build_style_runs(storage, char_styles):
    """Character style runs, tracking which properties are explicit overrides.

    Pages writes overrides with proto2 presence: a char style that un-bolds a
    word inside a bold paragraph sets bold=false explicitly; one that doesn't
    touch bold leaves it unset and must inherit the paragraph's bold.
    """
    runs = []
    if not storage.HasField("table_char_style"):
        return runs
    full_len = len("".join(storage.text))
    for entry, start, end in _runs_bounds(storage.table_char_style.entries, full_len):
        run = dict(start=start, end=end, bold=False, italic=False, underline=False,
                   has_object=False, bold_overridden=False, italic_overridden=False,
                   underline_overridden=False, has_explicit_font_color=False,
                   highlight=None, text_color=None, text_color_hex=None)
        if entry.HasField("object"):
            run["has_object"] = True
            style = char_styles.get(entry.object.identifier)
            if style is not None and style.HasField("char_properties"):
                p = style.char_properties
                run["bold_overridden"] = p.HasField("bold")
                run["italic_overridden"] = p.HasField("italic")
                run["underline_overridden"] = p.HasField("underline")
                run["bold"] = p.bold if run["bold_overridden"] else False
                run["italic"] = p.italic if run["italic_overridden"] else False
                run["underline"] = (p.underline == iw.K_SINGLE_UNDERLINE) if run["underline_overridden"] else False
                run["highlight"] = pages_highlight_name(p)
                # has_font_color is ambiguous alone; resolved per line below.
                run["has_explicit_font_color"] = p.HasField("font_color")
                run["text_color"] = pages_text_color_name(p)
                run["text_color_hex"] = pages_text_color_hex(p, run["text_color"])
        runs.append(run)
    return runs


def build_comment_highlight_ranges(storage):
    """Comment highlights: open entries reference a HighlightArchive, close
    entries have no object. Nested ranges pair LIFO; unterminated opens drop."""
    if not storage.HasField("table_highlight"):
        return []
    ranges, open_starts = [], []
    for entry in storage.table_highlight.entries:
        identifier = str(entry.object.identifier) if entry.HasField("object") else ""
        if identifier == "":
            if open_starts:
                ranges.append((open_starts.pop(), entry.character_index))
        else:
            open_starts.append(entry.character_index)
    return ranges


def _find(runs, pos):
    for r in runs:
        if r["start"] <= pos < r["end"]:
            return r
    return None


def split_into_styled_paragraphs(full_text, style_runs, attachment_positions=None,
                                 image_temp_paths=None, para_style_runs=None,
                                 comment_highlight_ranges=None):
    attachment_positions = attachment_positions or {}
    image_temp_paths = image_temp_paths or {}
    para_style_runs = para_style_runs or []
    comment_highlight_ranges = comment_highlight_ranges or []
    paragraphs = []
    char_pos = 0

    for line in full_text.split("\n"):
        line_start = char_pos
        line_end = char_pos + len(line)
        trimmed = re.sub(r"\t+\Z", "", line)  # Pages inserts meaningless trailing tabs
        trimmed_end = line_start + len(trimmed)

        # One image paragraph per U+FFFC attachment on the line.
        emitted_image = False
        idx = trimmed.find("￼")
        while idx != -1:
            zip_path = attachment_positions.get(line_start + idx)
            if zip_path and image_temp_paths.get(zip_path):
                paragraphs.append({"text": trimmed, "segments": [], "image": image_temp_paths[zip_path]})
                emitted_image = True
            idx = trimmed.find("￼", idx + 1)
        if emitted_image:
            char_pos = line_end + 1
            continue

        clean = trimmed.replace("￼", "")
        segments = []
        if not style_runs and not para_style_runs:
            if clean:
                segments.append(build(clean))
        else:
            def overlaps(r):
                return r["start"] < trimmed_end and r["end"] > line_start

            # In fully specified lines where some words are underlined, the
            # non-underlined words must not inherit paragraph bold.
            line_fully_specified = not any(not r["has_object"] and overlaps(r) for r in style_runs)
            line_has_underline = line_fully_specified and any(
                r["has_object"] and r["underline"] and overlaps(r) for r in style_runs)
            # Mixed per-run font_color overrides are author intent; uniform ones
            # are Pages materializing a default.
            line_char_runs = [r for r in style_runs if r["has_object"] and overlaps(r)]
            mixed_color = (any(r["has_explicit_font_color"] for r in line_char_runs)
                           and any(not r["has_explicit_font_color"] for r in line_char_runs))

            pos = line_start
            while pos < trimmed_end:
                cr = _find(style_runs, pos)
                pr = _find(para_style_runs, pos)
                if cr and cr["has_object"]:
                    bold = cr["bold"] if cr["bold_overridden"] else (pr["bold"] if pr else False)
                    italic, underline = cr["italic"], cr["underline"]
                    if line_has_underline and not cr["bold_overridden"] and not cr["underline_overridden"]:
                        bold = False
                elif pr:
                    bold, italic, underline = pr["bold"], pr["italic"], pr["underline"]
                else:
                    bold = italic = underline = False

                strike = pr["strike"] if pr else False
                font_size = pr["font_size"] if pr else None
                highlight = (cr and cr["highlight"]) or (pr["highlight"] if pr else None)
                if mixed_color and cr and cr["has_explicit_font_color"]:
                    text_color, text_color_hex = cr["text_color"], cr["text_color_hex"]
                else:
                    text_color = (cr and cr["text_color"]) or (pr["text_color"] if pr else None)
                    text_color_hex = (cr and cr["text_color_hex"]) or (pr["text_color_hex"] if pr else None)

                run_end = trimmed_end
                if cr:
                    run_end = min(run_end, cr["end"])
                if pr:
                    run_end = min(run_end, pr["end"])
                for cstart, cend in comment_highlight_ranges:
                    if cstart > pos:
                        run_end = min(run_end, cstart)
                    if cend > pos:
                        run_end = min(run_end, cend)
                commented = any(cs <= pos < ce for cs, ce in comment_highlight_ranges)
                text = full_text[pos:run_end].replace("￼", "")

                if text:
                    attrs = {"bold": bold, "italic": italic, "underline": underline,
                             "strike": strike, "font_size": font_size, "highlight": highlight,
                             "text_color": text_color, "text_color_hex": text_color_hex}
                    if commented:
                        attrs["comment_highlight"] = True
                    candidate = build(text, attrs)
                    if segments and same_attributes(segments[-1], candidate):
                        segments[-1]["text"] += text
                    else:
                        segments.append(candidate)
                pos = run_end

        segments = merge_punctuation_segments(segments)
        paragraphs.append({"text": clean, "segments": segments})
        char_pos = line_end + 1
    return paragraphs
