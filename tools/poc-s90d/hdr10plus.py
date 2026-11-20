# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LibreELEC Hades
"""HDR10+ (SMPTE ST 2094-40, application 4) for the S90D proof of concept.

One processing window, as every HDR10+ film and Hades' own metadata have it:

- Metadata: the values in the units the SEI codes them (MaxScl, AverageRGB and
  the percentiles in 1/100000 of 10000 cd/m2, i.e. 0.1 cd/m2; knee point in
  1/4095; anchors in 1/1023), read from and written to the JSON of
  hdr10plus_tool (one entry of SceneInfo per frame, display order).
- t35_payload() / parse_t35(): the ITU-T T.35 message of the SEI, from the
  country code on (what FFmpeg's av_dynamic_hdr_plus_from_t35() reads after
  the provider codes).
- vsif(): the 27-byte payload of the HDMI vendor-specific InfoFrame
  (PB1..PB27), as Hades sends it: a port of MakeVsif() of
  xbmc/utils/HDR10PlusVsif.h (kodi-9280 and the patches after it).
- HEVC Annex B: split NAL units, wrap a payload in a prefix SEI, insert one
  before the first slice of each access unit (streams without reordering).
"""

import json
from dataclasses import dataclass, field

PERCENTAGES_9 = (1, 5, 10, 25, 50, 75, 90, 95, 99)
PERCENTAGES_10 = (1, 5, 10, 25, 50, 75, 90, 95, 98, 99)
VSIF_SIZE = 27
OUI = (0x8B, 0x84, 0x90)  # 90-84-8B, least significant byte first


@dataclass
class Metadata:
    application_version: int = 1
    targeted_max_luminance: int = 0  # cd/m2, 0 = not given
    maxscl: tuple = (0, 0, 0)
    average_maxrgb: int = 0
    percentages: tuple = PERCENTAGES_9
    percentiles: tuple = (0,) * 9
    fraction_bright_pixels: int = 0  # 1/1000
    knee_x: int = 0  # 1/4095
    knee_y: int = 0
    anchors: tuple = ()  # 1/1023 each; empty = no tone mapping
    tone_mapping: bool = False

    def nits(self, coded):
        return coded / 10.0


# ---------------------------------------------------------------- JSON (hdr10plus_tool)

def to_json_scene(m, frame, scene, scene_first):
    lum = {
        "AverageRGB": m.average_maxrgb,
        "LuminanceDistributions": {
            "DistributionIndex": list(m.percentages),
            "DistributionValues": list(m.percentiles),
        },
        "MaxScl": list(m.maxscl),
    }
    entry = {}
    if m.tone_mapping:
        entry["BezierCurveData"] = {
            "Anchors": list(m.anchors),
            "KneePointX": m.knee_x,
            "KneePointY": m.knee_y,
        }
    entry.update({
        "LuminanceParameters": lum,
        "NumberOfWindows": 1,
        "TargetedSystemDisplayMaximumLuminance": m.targeted_max_luminance,
        "SceneFrameIndex": frame - scene_first,
        "SceneId": scene,
        "SequenceFrameIndex": frame,
    })
    return entry


def dump_json(frames, path, tool="hades-poc-s90d"):
    """frames: one Metadata per frame, display order. Scenes = runs of equal metadata."""
    scenes = []
    firsts, counts = [], []
    scene, first = -1, 0
    for i, m in enumerate(frames):
        if i == 0 or m != frames[i - 1]:
            scene += 1
            first = i
            firsts.append(i)
            counts.append(0)
        counts[-1] += 1
        scenes.append(to_json_scene(m, i, scene, first))
    root = {
        "JSONInfo": {"HDR10plusProfile": "B" if any(f.tone_mapping for f in frames) else "A",
                     "Version": "1.0"},
        "SceneInfo": scenes,
        "SceneInfoSummary": {"SceneFirstFrameIndex": firsts, "SceneFrameNumbers": counts},
        "ToolInfo": {"Tool": tool, "Version": "1"},
    }
    with open(path, "w") as f:
        json.dump(root, f, indent=1)


def load_json(path, application_version=1):
    """The frames of an hdr10plus_tool JSON, in display order (SequenceFrameIndex).
    The JSON does not carry the application version: it is given."""
    with open(path) as f:
        root = json.load(f)
    entries = sorted(root["SceneInfo"], key=lambda e: e.get("SequenceFrameIndex", 0))
    out = []
    for e in entries:
        lum = e["LuminanceParameters"]
        dist = lum.get("LuminanceDistributions", {})
        bez = e.get("BezierCurveData")
        out.append(Metadata(
            application_version=application_version,
            targeted_max_luminance=int(e.get("TargetedSystemDisplayMaximumLuminance", 0)),
            maxscl=tuple(int(v) for v in lum["MaxScl"]),
            average_maxrgb=int(lum["AverageRGB"]),
            percentages=tuple(int(v) for v in dist.get("DistributionIndex", ())),
            percentiles=tuple(int(v) for v in dist.get("DistributionValues", ())),
            knee_x=int(bez["KneePointX"]) if bez else 0,
            knee_y=int(bez["KneePointY"]) if bez else 0,
            anchors=tuple(int(v) for v in bez["Anchors"]) if bez else (),
            tone_mapping=bez is not None,
        ))
    return out


# ---------------------------------------------------------------- the T.35 message

class _BitWriter:
    def __init__(self):
        self.bits = []

    def put(self, value, n):
        if value < 0 or value >= (1 << n):
            raise ValueError(f"{value} does not fit in {n} bits")
        for i in range(n - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def bytes(self):
        bits = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


class _BitReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def get(self, n):
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v


def t35_payload(m):
    """country 0xB5, provider 0x003C, oriented 0x0001, application 4, then ST 2094-40."""
    w = _BitWriter()
    for v, n in ((0xB5, 8), (0x003C, 16), (0x0001, 16), (4, 8), (m.application_version, 8)):
        w.put(v, n)
    w.put(1, 2)  # num_windows
    w.put(m.targeted_max_luminance, 27)
    w.put(0, 1)  # targeted_system_display_actual_peak_luminance_flag
    for v in m.maxscl:
        w.put(v, 17)
    w.put(m.average_maxrgb, 17)
    if len(m.percentages) != len(m.percentiles) or len(m.percentages) > 15:
        raise ValueError("distribution: as many percentages as percentiles, 15 at most")
    w.put(len(m.percentages), 4)
    for p, v in zip(m.percentages, m.percentiles):
        w.put(p, 7)
        w.put(v, 17)
    w.put(m.fraction_bright_pixels, 10)
    w.put(0, 1)  # mastering_display_actual_peak_luminance_flag
    w.put(1 if m.tone_mapping else 0, 1)
    if m.tone_mapping:
        w.put(m.knee_x, 12)
        w.put(m.knee_y, 12)
        w.put(len(m.anchors), 4)
        for a in m.anchors:
            w.put(a, 10)
    w.put(0, 1)  # color_saturation_mapping_flag
    return w.bytes()


def parse_t35(data):
    """The inverse of t35_payload() (one window, no peak luminance tables)."""
    r = _BitReader(data)
    if (r.get(8), r.get(16), r.get(16), r.get(8)) != (0xB5, 0x003C, 0x0001, 4):
        raise ValueError("not an HDR10+ (ST 2094-40 application 4) message")
    m = Metadata(application_version=r.get(8))
    if r.get(2) != 1:
        raise ValueError("only one processing window is read")
    m.targeted_max_luminance = r.get(27)
    if r.get(1):
        raise ValueError("targeted display actual peak luminance tables are not read")
    m.maxscl = tuple(r.get(17) for _ in range(3))
    m.average_maxrgb = r.get(17)
    n = r.get(4)
    pairs = [(r.get(7), r.get(17)) for _ in range(n)]
    m.percentages = tuple(p for p, _ in pairs)
    m.percentiles = tuple(v for _, v in pairs)
    m.fraction_bright_pixels = r.get(10)
    if r.get(1):
        raise ValueError("mastering display actual peak luminance tables are not read")
    m.tone_mapping = bool(r.get(1))
    if m.tone_mapping:
        m.knee_x = r.get(12)
        m.knee_y = r.get(12)
        m.anchors = tuple(r.get(10) for _ in range(r.get(4)))
    return m


# ---------------------------------------------------------------- the InfoFrame (Hades' MakeVsif())

def _coded_maxrgb(coded):
    v = coded // 10
    return min(255, (v + 8) >> 4)


def distribution_from(m):
    """The entry of the distribution that each of the nine places takes, or None."""
    n = len(m.percentages)
    if n not in (9, 10):
        return None
    out, entry = [], 0
    for i in range(9):
        if n == 10 and i == 8:
            if m.percentages[entry] != 98:
                return None
            entry += 1
        if m.percentages[entry] != PERCENTAGES_9[i]:
            return None
        out.append(entry)
        entry += 1
    return out


def vsif(m):
    """The 27 bytes Hades sends for these metadata, or (None, why)."""
    if m.application_version > 1:
        return None, "an application version above 1"
    places = distribution_from(m)
    if places is None:
        return None, "a distribution of maxRGB that is not the one its InfoFrame carries"
    p = bytearray(VSIF_SIZE)
    p[0:3] = bytes(OUI)
    t = m.targeted_max_luminance
    tcode = min(31, (t + 16) >> 5) if t < 1024 else 31
    p[3] = ((m.application_version & 3) << 6) | ((tcode & 0x1F) << 1)
    p[4] = _coded_maxrgb(m.average_maxrgb)
    for i in range(9):
        coded = m.percentiles[places[i]]
        p[5 + i] = min(255, coded) if (i == 2 and m.application_version != 0) else _coded_maxrgb(coded)
    if m.tone_mapping:
        anchors = min(len(m.anchors), 9)
        kx = min(1023, (m.knee_x + 2) >> 2)
        ky = min(1023, (m.knee_y + 2) >> 2)
        p[14] = ((anchors & 0xF) << 4) | ((kx >> 6) & 0xF)
        p[15] = ((kx & 0x3F) << 2) | ((ky >> 8) & 0x3)
        p[16] = ky & 0xFF
        for i in range(anchors):
            p[17 + i] = min(255, (m.anchors[i] + 2) >> 2)
    p[26] = 0x40  # graphics overlay 0, no delay 1
    return bytes(p), None


def vsif_from_hex(text):
    """A logged packet as the 27 bytes of its payload. Takes the payload alone (27 bytes),
    or the whole InfoFrame: header 81 01 1B, checksum, payload (31 bytes)."""
    b = bytes.fromhex("".join(text.split()).replace(":", ""))
    if len(b) == 31 and b[0] == 0x81:
        if b[2] != VSIF_SIZE:
            raise ValueError(f"InfoFrame length {b[2]}, not 27")
        if sum(b) & 0xFF:
            raise ValueError("InfoFrame checksum does not hold")
        b = b[4:]
    if len(b) != VSIF_SIZE:
        raise ValueError(f"{len(b)} bytes: neither a payload (27) nor an InfoFrame (31)")
    if tuple(b[0:3]) != OUI:
        raise ValueError("not the OUI of HDR10+ (90-84-8B)")
    return b


def describe_vsif(p):
    return {
        "version": p[3] >> 6,
        "target": ((p[3] >> 1) & 0x1F) * 32,
        "average": p[4] * 16,
        "percentiles": [p[5 + i] for i in range(9)],
        "anchors": p[14] >> 4,
        "overlay": bool(p[26] & 0x80),
    }


# ---------------------------------------------------------------- HEVC Annex B

def split_nals(data):
    """(start, end) of each NAL unit (after its start code) in an Annex B stream."""
    starts = []
    i, n = 0, len(data)
    while i + 3 <= n:
        if data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1:
            starts.append(i + 3)
            i += 3
        else:
            i += 1
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] - 3 if k + 1 < len(starts) else n
        while e > s and data[e - 1] == 0:  # trailing zero bytes / zero_byte of a 4-byte start code
            e -= 1
        out.append((s, e))
    return out


def nal_type(nal):
    return (nal[0] >> 1) & 0x3F


def _escape(rbsp):
    out = bytearray()
    zeros = 0
    for b in rbsp:
        if zeros >= 2 and b <= 3:
            out.append(3)
            zeros = 0
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


def _unescape(ebsp):
    out = bytearray()
    zeros = 0
    for b in ebsp:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


def sei_nal(payload):
    """A prefix SEI NAL unit (type 39) with one user_data_registered_itu_t_t35 message."""
    body = bytearray([4])  # payloadType
    size = len(payload)
    while size >= 255:
        body.append(255)
        size -= 255
    body.append(size)
    body += payload
    body.append(0x80)  # rbsp_trailing_bits
    return bytes([39 << 1, 1]) + _escape(bytes(body))


def sei_t35_messages(nal):
    """The T.35 payloads of a prefix SEI NAL unit."""
    rbsp = _unescape(nal[2:])
    i, out = 0, []
    while i < len(rbsp) and not (rbsp[i] == 0x80 and i == len(rbsp) - 1):
        ptype = 0
        while rbsp[i] == 255:
            ptype += 255
            i += 1
        ptype += rbsp[i]
        i += 1
        psize = 0
        while rbsp[i] == 255:
            psize += 255
            i += 1
        psize += rbsp[i]
        i += 1
        if ptype == 4:
            out.append(rbsp[i:i + psize])
        i += psize
    return out


def _first_slice(nal):
    return nal_type(nal) < 32 and len(nal) > 2 and (nal[2] & 0x80)


def insert_sei(data, payloads):
    """The stream with payloads[k] in a prefix SEI before the first slice of access unit k
    (decode order, which is the display order of a stream without reordering)."""
    out = bytearray()
    au = 0
    for s, e in split_nals(data):
        nal = data[s:e]
        if _first_slice(nal):
            if au >= len(payloads):
                raise ValueError(f"more access units than metadata ({len(payloads)})")
            if payloads[au] is not None:
                out += b"\x00\x00\x00\x01" + sei_nal(payloads[au])
            au += 1
        out += b"\x00\x00\x00\x01" + nal
    if au != len(payloads):
        raise ValueError(f"{au} access units, {len(payloads)} metadata")
    return bytes(out)


def extract_sei(data):
    """The HDR10+ metadata of each access unit in decode order (None where it has none)."""
    out = []
    pending = None
    for s, e in split_nals(data):
        nal = data[s:e]
        t = nal_type(nal)
        if t == 39:
            for msg in sei_t35_messages(nal):
                try:
                    pending = parse_t35(msg)
                except ValueError:
                    pass
        elif _first_slice(nal):
            out.append(pending)
            pending = None
    return out
