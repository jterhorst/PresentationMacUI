# Ported from pp7gen (https://github.com/WorshipKit/pp7gen), Apache-2.0,
# Copyright WorshipKit. Sources: lib/pp7gen/segment.rb, text_color.rb,
# highlight_color.rb, and merge_punctuation_segments in paragraph_helpers.rb.
"""Styled text segments: the atom both readers emit."""
import math
import re
import unicodedata

# Segment attributes (segment.rb). Defaults are always present; optional
# attributes appear only when set.
DEFAULTS = {
    "bold": False,
    "italic": False,
    "underline": False,
    "highlight": None,
    "strike": False,
    "font_size": None,
    "text_color": None,
}
OPTIONAL_ATTRIBUTES = ("text_color_hex", "comment_highlight")
ATTRIBUTES = tuple(DEFAULTS) + OPTIONAL_ATTRIBUTES


def build(text, attrs=None):
    attrs = attrs or {}
    seg = {"text": str(text)}
    for name, default in DEFAULTS.items():
        seg[name] = attrs[name] if name in attrs else default
    for name in OPTIONAL_ATTRIBUTES:
        if attrs.get(name) is not None:
            seg[name] = attrs[name]
    return seg


def same_attributes(one, other):
    if one is None or other is None:
        return False
    return all(one.get(n) == other.get(n) for n in ATTRIBUTES)


def ruby_round(x):
    """Ruby Float#round: half away from zero (Python's round() is banker's)."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


# --- text_color.rb ----------------------------------------------------------

class TextColor:
    REFERENCE_RGB = {
        "red": (200, 50, 50),
        "blue": (50, 100, 200),
        "green": (40, 150, 70),
        "gold": (220, 180, 30),
        "orange": (230, 130, 30),
        "purple": (130, 60, 170),
        "pink": (230, 45, 140),
    }
    GREY_MAX_CHANNEL_SPREAD = 50
    WHITE_MIN_CHANNEL = 240

    @classmethod
    def from_rgb(cls, r, g, b):
        if r is None or g is None or b is None:
            return None
        if cls._near_white(r, g, b):
            return "white"
        if cls._near_grey(r, g, b):
            return None
        return min(cls.REFERENCE_RGB.items(),
                   key=lambda kv: (r - kv[1][0]) ** 2 + (g - kv[1][1]) ** 2 + (b - kv[1][2]) ** 2)[0]

    @classmethod
    def from_hex(cls, value):
        if value is None:
            return None
        digits = re.sub(r"\A#", "", str(value))
        if not re.fullmatch(r"[0-9a-fA-F]{6}", digits):
            return None
        return cls.from_rgb(int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))

    @classmethod
    def from_docx_value(cls, value):
        if value is None or str(value) == "" or str(value) == "auto":
            return None
        return cls.from_hex(value)

    @classmethod
    def _near_grey(cls, r, g, b):
        s = cls.GREY_MAX_CHANNEL_SPREAD
        return abs(r - g) <= s and abs(g - b) <= s and abs(r - b) <= s

    @classmethod
    def _near_white(cls, r, g, b):
        m = cls.WHITE_MIN_CHANNEL
        return cls._near_grey(r, g, b) and r >= m and g >= m and b >= m


# --- highlight_color.rb -----------------------------------------------------

class HighlightColor:
    REFERENCE_RGB = {
        "yellow": (255, 255, 0),
        "green": (0, 255, 0),
        "blue": (0, 0, 255),
        "pink": (255, 0, 255),
        "orange": (255, 165, 0),
    }
    DOCX_ALIASES = {
        "yellow": "yellow", "darkYellow": "yellow",
        "green": "green", "darkGreen": "green",
        "blue": "blue", "darkBlue": "blue", "cyan": "blue", "darkCyan": "blue",
        "magenta": "pink", "darkMagenta": "pink", "red": "pink", "darkRed": "pink",
    }
    GREY_SQUARED_TOLERANCE = 24_300

    @classmethod
    def from_docx_name(cls, value):
        if value is None:
            return None
        return cls.DOCX_ALIASES.get(str(value))

    @classmethod
    def from_rgb(cls, r, g, b):
        if r is None or g is None or b is None:
            return None
        if cls._near_grey(r, g, b):
            return None
        return min(cls.REFERENCE_RGB.items(),
                   key=lambda kv: (r - kv[1][0]) ** 2 + (g - kv[1][1]) ** 2 + (b - kv[1][2]) ** 2)[0]

    @classmethod
    def _near_grey(cls, r, g, b):
        return any((r - x) ** 2 + (g - y) ** 2 + (b - z) ** 2 < cls.GREY_SQUARED_TOLERANCE
                   for x, y, z in ((255, 255, 255), (0, 0, 0)))


# --- merge_punctuation_segments (paragraph_helpers.rb) ----------------------

def punctuation_only(text):
    """Ruby /\\A[\\p{P}\\p{Z}\\s]+\\z/."""
    return bool(text) and all(
        unicodedata.category(c)[0] in ("P", "Z") or c in " \t\n\v\f\r" for c in text)


def merge_punctuation_segments(segments):
    # 2-segment leading punctuation (phantom-run-was-skipped case).
    if len(segments) == 2 and punctuation_only(segments[0]["text"]):
        segments[1]["text"] = segments[0]["text"] + segments[1]["text"]
        segments.pop(0)
    if len(segments) < 3:
        return segments
    i = 1
    while i < len(segments) - 1:
        seg, prev_seg, next_seg = segments[i], segments[i - 1], segments[i + 1]
        if punctuation_only(seg["text"]) and same_attributes(prev_seg, next_seg):
            prev_seg["text"] += seg["text"] + next_seg["text"]
            del segments[i + 1]
            del segments[i]
        else:
            i += 1
    return segments


def ruby_strip(text):
    """Ruby String#strip: ASCII whitespace and NUL only (not NBSP)."""
    return text.strip(" \t\n\v\f\r\0")
