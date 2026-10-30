"""Read and write skew masks in Toolbag 5 scene files (.tbscene).

Toolbag's Python API has no access to skew masks; the scene file has. Layout as found on
Toolbag 5.0.2 by comparing saves before and after painting one dot:

* The file is a gapless chain of blocks: ``tag[8] count:u32 size:u32``, then ``count``
  entries ``name[8] offset:u32 length:u32``, then ``size`` bytes of data. There are no
  enclosing lengths, so inserting a block needs no other correction.
* One ``DBakeSO`` block per low poly target. Fields: ``maskcoun``, per mask ``dirmsk<i>``
  (mask present), ``dirmkE<i>`` (mask enabled), ``mkTx<i>``/``mkTy<i>`` (edge length, 0 for
  the default 512).
* Mask i is a separate block ``BDSOdm<i>`` right behind the ``DBakeSO`` (count 0). Its
  content is a square DDS file, D3DFMT_L16: 0xFFFF = averaged (no correction), 0 = full
  correction. Row 0 is at the top of Toolbag's 2D view, where v = 1.
* The next ``SObject`` block after the ``DBakeSO`` is the target's first low mesh; its field
  ``name`` (u32 length + text) identifies the target.
"""

from __future__ import annotations

import os
import re
import struct

DEFAULT_SIZE = 512  # Toolbag's own mask size; mkTx/mkTy hold 0 then


def _dds_header(size: int) -> bytes:
    return struct.pack(
        "<4s31I", b"DDS ", 124, 0x1007, size, size, size * size * 2, 0, 1,
        *([0] * 11), 32, 4, 81, 16, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF,
        0x1000, 0, 0, 0, 0)


_HEADER_LEN = len(_dds_header(DEFAULT_SIZE))


def chunks(data: bytes) -> list[tuple[str, int, int, dict[str, tuple[int, int]]]]:
    """(tag, start, end, fields) per block; fields map a name to (absolute offset, length)."""
    out = []
    pos = 0
    while pos < len(data):
        tag = data[pos:pos + 8].rstrip(b"\0").decode("latin1")
        count, size = struct.unpack_from("<II", data, pos + 8)
        table = pos + 16
        body = table + 16 * count
        fields = {}
        for k in range(count):
            entry = table + 16 * k
            name = data[entry:entry + 8].rstrip(b"\0").decode("latin1")
            off, length = struct.unpack_from("<II", data, entry + 8)
            fields[name] = (body + off, length)
        end = body + size
        if end > len(data):
            raise ValueError("Block %s at %d runs past the end of the file" % (tag, pos))
        out.append((tag, pos, end, fields))
        pos = end
    return out


def targets(data: bytes) -> list[dict]:
    """One entry per low poly target: first low mesh name, DBakeSO fields, mask blocks."""
    cs = chunks(data)
    result = []
    for idx, (tag, start, end, fields) in enumerate(cs):
        if tag != "DBakeSO":
            continue
        masks = {}
        k = idx + 1
        while k < len(cs) and cs[k][0].startswith("BDSO"):
            masks[cs[k][0]] = (cs[k][1], cs[k][2])
            k += 1
        name = None
        for tag2, _s, _e, f2 in cs[k:]:
            if tag2 == "SObject":
                off, _length = f2["name"]
                n = struct.unpack_from("<I", data, off)[0]
                name = data[off + 4:off + 4 + n].decode("utf-8")
                break
        count = struct.unpack_from("<I", data, fields["maskcoun"][0])[0]
        result.append({"name": name, "start": start, "end": end, "fields": fields,
                       "maskcount": count, "masks": masks})
    return result


def _find(data: bytes, low_name: str) -> dict:
    hits = [t for t in targets(data) if t["name"] == low_name]
    if not hits:
        raise KeyError("No bake target with low mesh %r in the scene file" % low_name)
    if len(hits) > 1:
        raise KeyError("Several bake targets with low mesh %r; rename them first" % low_name)
    return hits[0]


def read_skew(data: bytes, low_name: str, index: int = 0) -> tuple[int, bytes] | None:
    """(edge length, size*size bytes with 0 = full correction) or None if nothing is set."""
    t = _find(data, low_name)
    block = t["masks"].get("BDSOdm%d" % index)
    if block is None:
        return None
    dds = data[block[0] + 16:block[1]]
    size = struct.unpack_from("<I", dds, 12)[0]
    return size, bytes(dds[_HEADER_LEN + 1::2])


def write_skew(data: bytes, low_name: str, pixels: bytes, index: int = 0) -> bytes:
    """Scene data with the skew mask set. ``pixels``: size*size bytes, square, row 0 at the
    top. Sizes other than 512 also go into mkTx<i>/mkTy<i>."""
    size = int(round(len(pixels) ** 0.5))
    if size * size != len(pixels):
        raise ValueError("A mask must be square, got %d bytes" % len(pixels))
    t = _find(data, low_name)
    if index >= t["maskcount"]:
        raise IndexError("Target %s has only %d masks" % (low_name, t["maskcount"]))
    doubled = bytearray(2 * len(pixels))  # 8 bit -> L16: both bytes equal
    doubled[0::2] = pixels
    doubled[1::2] = pixels
    payload = _dds_header(size) + bytes(doubled)
    tag = ("BDSOdm%d" % index).encode("latin1").ljust(8, b"\0")
    block = tag + struct.pack("<II", 0, len(payload)) + payload

    out = bytearray(data)
    for flag in ("dirmsk%d" % index, "dirmkE%d" % index):
        out[t["fields"][flag][0]] = 1
    for name in ("mkTx%d" % index, "mkTy%d" % index):
        off = t["fields"][name][0]
        out[off:off + 4] = struct.pack("<I", 0 if size == DEFAULT_SIZE else size)
    old = t["masks"].get("BDSOdm%d" % index)
    if old is not None:
        out[old[0]:old[1]] = block
    else:  # behind the target's existing mask blocks
        insert_at = max([t["end"]] + [e for _s, e in t["masks"].values()])
        out[insert_at:insert_at] = block
    return bytes(out)


def new_skew_path(scene: str, folder: str | None = None) -> str:
    """``<name>_skew.tbscene`` next to the scene (or in ``folder``), never an existing file.
    Forward slashes: backslashes break Toolbag's autosave."""
    stem = os.path.splitext(scene.replace("\\", "/"))[0]
    if folder:
        stem = folder.replace("\\", "/").rstrip("/") + "/" + os.path.basename(stem)
    stem = re.sub(r"_skew(_\d+)?$", "", stem)
    candidate, n = stem + "_skew.tbscene", 2
    while os.path.exists(candidate):
        candidate, n = "%s_skew_%d.tbscene" % (stem, n), n + 1
    return candidate
