#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LibreELEC Hades
"""A synthetic HDR10+ clip for frame-exact timing tests (docs/poc-s90d-hdr10plus.md, Phase 0.5).

One still PQ / BT.2020 picture whose HDR10+ metadata switch between two states at known
frames, so that a display that follows them maps the same pixels differently:

- LOW:  the metadata say the scene peaks at 1000 cd/m2 (a ~1000 cd/m2 panel maps little);
- HIGH: they say 4000 cd/m2 (the panel compresses: the patches of 600 to 1000 cd/m2 dim).

The picture: a row of grey patches (100 ... 4000 cd/m2), a marker square at the top left that
is lit (100 cd/m2, which neither state should change) on HIGH frames and black on LOW frames,
and a 12-bit frame counter along the bottom. Filmed at 240 fps, the marker gives the frame the
picture is at and the patches give the frame the metadata took effect at; their difference is
the offset. The TV may smooth metadata over time: the blocks still show the offset where single
frames (the pulses) do not.

Schedule: LOW lead-in, then alternate blocks of --block frames, then --pulses single HIGH frames
inside LOW, every --pulse-gap frames. Writes, into --out:
  clip.hevc, clip.mkv   the stream (x265, no B-frames, so decode order = display order), with one
                        ST 2094-40 SEI per picture and HDR10 static metadata;
  metadata.json         the metadata of each frame in hdr10plus_tool's format (the reference for
                        compare_hdr10plus.py, like `hdr10plus_tool extract` of a film);
  schedule.tsv          frame, state, marker, counter.
"""

import argparse
import fractions
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdr10plus  # noqa: E402

PATCH_NITS = (100, 200, 400, 600, 800, 1000, 1500, 2000, 4000)
MARKER_NITS = 100
COUNTER_BITS = 12

LOW = hdr10plus.Metadata(
    application_version=1, maxscl=(10000, 10000, 10000), average_maxrgb=1000,
    percentiles=(10, 50, 100, 300, 1000, 2000, 5000, 8000, 10000))
HIGH = hdr10plus.Metadata(
    application_version=1, maxscl=(40000, 40000, 40000), average_maxrgb=2000,
    percentiles=(10, 50, 100, 300, 1000, 4000, 15000, 20000, 40000))


def pq_code(nits):
    """10-bit limited-range code of a PQ (SMPTE ST 2084) luminance."""
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    y = (max(nits, 0) / 10000) ** m1
    e = ((c1 + c2 * y) / (1 + c3 * y)) ** m2
    return int(round(64 + 876 * e))


def schedule(lead, block, blocks, pulses, gap):
    states = ["LOW"] * lead
    for b in range(blocks):
        states += ["HIGH" if b % 2 == 0 else "LOW"] * block
    for _ in range(pulses):
        states += ["LOW"] * (gap - 1) + ["HIGH"]
    states += ["LOW"] * gap
    return states


class Picture:
    def __init__(self, w, h):
        self.w, self.h = w, h
        y = np.full((h, w), pq_code(0), dtype=np.uint16)
        pw, ph = w // 12, h // 4
        step = w // (len(PATCH_NITS) + 1)
        top = (h - ph) // 2
        for i, nits in enumerate(PATCH_NITS):
            left = step * (i + 1) - pw // 2
            y[top:top + ph, left:left + pw] = pq_code(nits)
        self.base = y
        self.uv = np.full((h // 2, w // 2), 512, dtype=np.uint16).tobytes() * 2
        self.marker = h // 6
        self.bit = w // (COUNTER_BITS + 4)

    def frame(self, index, lit):
        y = self.base.copy()
        m = self.marker
        y[m // 4:m // 4 + m, m // 4:m // 4 + m] = pq_code(MARKER_NITS if lit else 0)
        b = self.bit
        top = self.h - b - b // 4
        for k in range(COUNTER_BITS):
            on = (index >> (COUNTER_BITS - 1 - k)) & 1
            left = b * (k + 2)
            y[top:top + b - b // 8, left:left + b - b // 8] = pq_code(MARKER_NITS if on else 2)
        return y.tobytes() + self.uv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", default="24000/1001")
    ap.add_argument("--size", default="3840x2160")
    ap.add_argument("--lead", type=int, default=96, help="LOW frames at the start")
    ap.add_argument("--block", type=int, default=48)
    ap.add_argument("--blocks", type=int, default=20)
    ap.add_argument("--pulses", type=int, default=20)
    ap.add_argument("--pulse-gap", type=int, default=24)
    ap.add_argument("--preset", default="ultrafast")
    ap.add_argument("--crf", default="16")
    a = ap.parse_args()

    fps = fractions.Fraction(a.fps)
    w, h = (int(v) for v in a.size.split("x"))
    if w % 2 or h % 2:
        sys.exit("the size must be even")
    states = schedule(a.lead, a.block, a.blocks, a.pulses, a.pulse_gap)
    os.makedirs(a.out, exist_ok=True)
    raw = os.path.join(a.out, "plain.hevc")
    x265 = ":".join([
        "bframes=0", f"keyint={max(1, round(fps))}", "repeat-headers=1", "log-level=error",
        "colorprim=bt2020", "transfer=smpte2084", "colormatrix=bt2020nc", "range=limited",
        "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(40000000,50)",
        "max-cll=4000,400", "hdr10=1"])
    enc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
         "-pix_fmt", "yuv420p10le", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
         "-c:v", "libx265", "-preset", a.preset, "-crf", a.crf, "-x265-params", x265,
         "-f", "hevc", raw], stdin=subprocess.PIPE)
    pic = Picture(w, h)
    for i, s in enumerate(states):
        enc.stdin.write(pic.frame(i, s == "HIGH"))
    enc.stdin.close()
    if enc.wait():
        sys.exit("ffmpeg failed")

    frames = [HIGH if s == "HIGH" else LOW for s in states]
    with open(raw, "rb") as f:
        data = f.read()
    out = hdr10plus.insert_sei(data, [hdr10plus.t35_payload(m) for m in frames])
    clip = os.path.join(a.out, "clip.hevc")
    with open(clip, "wb") as f:
        f.write(out)
    os.remove(raw)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-r", str(fps), "-i", clip,
                    "-c", "copy", os.path.join(a.out, "clip.mkv")], check=True)
    hdr10plus.dump_json(frames, os.path.join(a.out, "metadata.json"))
    with open(os.path.join(a.out, "schedule.tsv"), "w") as f:
        f.write("frame\tstate\tmarker\tcounter\n")
        for i, s in enumerate(states):
            f.write(f"{i}\t{s}\t{1 if s == 'HIGH' else 0}\t{i % (1 << COUNTER_BITS)}\n")
    changes = sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])
    print(f"{len(states)} frames at {fps} fps ({len(states) / fps:.1f} s), {changes} metadata changes: {a.out}")


if __name__ == "__main__":
    main()
