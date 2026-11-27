# Ported from pp7gen (https://github.com/WorshipKit/pp7gen), Apache-2.0,
# Copyright WorshipKit. Source: lib/pp7gen/strategies/pages_input_strategy.rb
# (decompress_iwa, snappy_decompress, read_varint, parse_iwa_into_object_map).
"""iWork Archive (.iwa) container: Snappy-framed protobuf object streams.

Message schemas come from iwork.desc, a FileDescriptorSet extracted from
pp7gen's generated iWork stubs (see ../extract_descriptors.rb). The same file
feeds `protoc --descriptor_set_in` for a native port.
"""
import pathlib

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

_DESC = pathlib.Path(__file__).with_name("iwork.desc")


def _load_messages():
    fds = descriptor_pb2.FileDescriptorSet.FromString(_DESC.read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for f in fds.file:  # stored in dependency order
        pool.Add(f)
    names = ["TSP.ArchiveInfo", "TSWP.StorageArchive", "TSWP.CharacterStyleArchive",
             "TSWP.ParagraphStyleArchive", "TSP.PackageMetadata", "TSD.ImageArchive",
             "TSWP.DrawableAttachmentArchive"]
    return {n: message_factory.GetMessageClass(pool.FindMessageTypeByName(n)) for n in names}


M = _load_messages()
ArchiveInfo = M["TSP.ArchiveInfo"]
StorageArchive = M["TSWP.StorageArchive"]
CharacterStyleArchive = M["TSWP.CharacterStyleArchive"]
ParagraphStyleArchive = M["TSWP.ParagraphStyleArchive"]
PackageMetadata = M["TSP.PackageMetadata"]
ImageArchive = M["TSD.ImageArchive"]
DrawableAttachmentArchive = M["TSWP.DrawableAttachmentArchive"]

# Enum values used by the reader.
BODY = StorageArchive.DESCRIPTOR.enum_types_by_name["KindType"].values_by_name["BODY"].number
_CHAR_PROPS = CharacterStyleArchive.DESCRIPTOR.fields_by_name["char_properties"].message_type
K_SINGLE_UNDERLINE = _CHAR_PROPS.enum_types_by_name["UnderlineType"].values_by_name["kSingleUnderline"].number
K_NO_STRIKETHRU = _CHAR_PROPS.enum_types_by_name["StrikethruType"].values_by_name["kNoStrikethru"].number


def try_decode(cls, data):
    """Decode `data` as `cls`, or None. Mirrors the Ruby code's decode-and-rescue."""
    msg = cls()
    try:
        msg.ParseFromString(data)
    except Exception:  # google.protobuf.message.DecodeError and friends
        return None
    return msg


def read_varint(buf, pos):
    """Returns (value, new_pos) or (None, pos) at end of data."""
    result = shift = 0
    while True:
        if pos >= len(buf):
            return None, pos
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def snappy_decompress(compressed):
    """Minimal Snappy block decompressor. Returns bytes, or None on malformed input."""
    try:
        length, pos = read_varint(compressed, 0)
        if not length:
            return b""
        out = bytearray()
        n = len(compressed)
        while pos < n and len(out) < length:
            tag = compressed[pos]
            pos += 1
            kind = tag & 0x03
            if kind == 0:  # literal
                lit = (tag >> 2) + 1
                if lit > 60:
                    extra = lit - 60  # 1..4 little-endian length bytes
                    lit = int.from_bytes(compressed[pos:pos + extra], "little") + 1
                    pos += extra
                out += compressed[pos:pos + lit]
                pos += lit
                continue
            if kind == 1:
                copy_len = ((tag >> 2) & 0x07) + 4
                offset = ((tag >> 5) << 8) | compressed[pos]
                pos += 1
            elif kind == 2:
                copy_len = ((tag >> 2) & 0x3F) + 1
                offset = int.from_bytes(compressed[pos:pos + 2], "little")
                pos += 2
            else:
                copy_len = ((tag >> 2) & 0x3F) + 1
                offset = int.from_bytes(compressed[pos:pos + 4], "little")
                pos += 4
            if offset <= 0 or offset > len(out):
                continue  # Ruby copy_bytes ignores invalid offsets
            start = len(out) - offset
            if offset >= copy_len:
                out += out[start:start + copy_len]
            else:  # overlapping copy repeats the pattern byte by byte
                for i in range(copy_len):
                    out.append(out[start + i])
        return bytes(out[:length])
    except (IndexError, ValueError):
        return None


def decompress_iwa(data):
    """IWA framing: [type:1][length:3 LE][chunk]... type 0 = Snappy without CRC."""
    out = bytearray()
    pos, n = 0, len(data)
    while pos < n:
        header = data[pos:pos + 4]
        if len(header) < 4:
            break
        chunk_type = header[0]
        chunk_len = header[1] | (header[2] << 8) | (header[3] << 16)
        pos += 4
        if chunk_len == 0:
            break
        chunk = data[pos:pos + chunk_len]
        pos += chunk_len
        if len(chunk) < chunk_len:
            break
        if chunk_type == 0x00:
            d = snappy_decompress(chunk)
            if d:
                out += d
        elif chunk_type == 0x01:
            out += chunk
        # 0xff stream identifier and unknown types are skipped
    return bytes(out)


def parse_iwa_into_object_map(iwa_data, object_map):
    """Append every object in one .iwa file to object_map: id -> [{type, data}]."""
    buf = decompress_iwa(iwa_data)
    pos, n = 0, len(buf)
    while pos < n:
        info_len, pos = read_varint(buf, pos)
        if not info_len:
            break
        info_bytes = buf[pos:pos + info_len]
        pos += info_len
        if len(info_bytes) < info_len:
            break
        info = ArchiveInfo()
        try:
            info.ParseFromString(info_bytes)
        except Exception:
            break
        entries = object_map.setdefault(info.identifier, [])
        for mi in info.message_infos:
            data = buf[pos:pos + mi.length]
            pos += mi.length
            if len(data) < mi.length:
                break
            entries.append({"type": mi.type, "data": data})
