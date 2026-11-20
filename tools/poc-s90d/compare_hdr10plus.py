#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LibreELEC Hades
"""Line up what the source carries, what Hades sent and what the TV received, as HDR10+ InfoFrames
(docs/poc-s90d-hdr10plus.md, Phase 0.5). Every packet is compared as the 27 bytes of its payload.

Inputs (tab-separated; '#' lines and a header line are skipped; '-' = no HDR10+ packet):
  --ref  JSON of hdr10plus_tool (`hdr10plus_tool extract`, or metadata.json of make_timing_clip.py):
         the metadata of each source frame, turned into the InfoFrame Hades makes of it.
  --pc   the per-frame dump of Hades:   frame  flip_seq  flip_ns  packet
         frame = the source frame shown, flip_seq / flip_ns = the vblank sequence and time of the
         page-flip event that showed it.
  --tv   the log of the TV:             time_ns  counter  packet
         counter = a frame or vsync counter of the TV, '-' where there is none. Every received
         packet, or only the changes.
  A packet is 27 bytes of payload or the whole 31-byte InfoFrame (81 01 1B, checksum, payload).

Reports:
  ref vs pc  frame by frame: equal, different bytes, missing, and the frame offset that matches best;
  pc vs tv   the changes of each, matched in order: missing on the TV, extra, altered bytes, and for
             each matched change its offset in frames against the median offset of all of them. The
             two clocks are not shared, so a uniform delay does not show; changes that land a frame
             apart from the others do. The filmed clip of make_timing_clip.py gives the absolute delay.
Exit status: 0 when nothing differs, 1 when something does, 2 on unreadable input.
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdr10plus  # noqa: E402

OVERLAY = 0x80


def norm(p, keep_overlay):
    if p is None or keep_overlay:
        return p
    b = bytearray(p)
    b[26] &= ~OVERLAY & 0xFF
    return bytes(b)


def rows(path, ncols):
    out = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) != ncols:
                raise ValueError(f"{path}:{n}: {len(cols)} columns, {ncols} expected")
            if n == 1 and not cols[0].lstrip("-").isdigit():
                continue  # header
            out.append((n, cols))
    return out


def packet(text, where):
    if text.strip() == "-":
        return None
    try:
        return hdr10plus.vsif_from_hex(text)
    except ValueError as e:
        raise ValueError(f"{where}: {e}")


def load_pc(path, keep):
    out = []
    for n, (frame, seq, ns, p) in rows(path, 4):
        out.append((int(frame), int(seq), int(ns), norm(packet(p, f"{path}:{n}"), keep)))
    return out


def load_tv(path, keep):
    out = []
    for n, (ns, counter, p) in rows(path, 3):
        out.append((int(ns), None if counter.strip() == "-" else int(counter),
                    norm(packet(p, f"{path}:{n}"), keep)))
    return out


def load_ref(path, version, keep):
    out = []
    for m in hdr10plus.load_json(path, version):
        p, _ = hdr10plus.vsif(m)
        out.append(norm(p, keep))
    return out


def diff_bytes(a, b):
    return [i for i in range(hdr10plus.VSIF_SIZE) if a[i] != b[i]]


def hexs(p):
    return "-" if p is None else p.hex()


# ---------------------------------------------------------------- ref vs pc

def ref_vs_pc(ref, pc, max_shift=4):
    by_frame = {f: p for f, _, _, p in pc}

    def score(shift):
        return sum(1 for f, p in by_frame.items() if 0 <= f + shift < len(ref) and ref[f + shift] == p)

    best = max(range(-max_shift, max_shift + 1), key=lambda s: (score(s), -abs(s)))
    lines, bad = [], 0
    for f in sorted(by_frame):
        p = by_frame[f]
        if not 0 <= f < len(ref):
            lines.append(f"frame {f}: not in the reference ({len(ref)} frames)")
            bad += 1
            continue
        r = ref[f]
        if r == p:
            continue
        bad += 1
        if p is None:
            lines.append(f"frame {f}: not sent (reference {hexs(r)})")
        elif r is None:
            lines.append(f"frame {f}: sent without metadata in the reference ({hexs(p)})")
        else:
            lines.append(f"frame {f}: bytes {diff_bytes(r, p)} differ: ref {hexs(r)} sent {hexs(p)}")
    head = (f"ref vs pc: {len(by_frame)} frames sent, {len(by_frame) - bad} as the reference, {bad} not; "
            f"best frame offset {best:+d} ({score(best)} equal; at 0: {score(0)})")
    return head, lines, bad


# ---------------------------------------------------------------- pc vs tv

def changes(seq):
    """(index into seq, packet) where the packet differs from the one before."""
    out, last = [], object()
    for i, item in enumerate(seq):
        p = item[-1]
        if p != last:
            out.append((i, p))
            last = p
    return out


def align(a, b, near=3):
    """Order-keeping match of two lists of packets: equal = 0, near (<= near bytes) = 1, a gap = 2.
    Returns (pairs (i, j, kind), unmatched a, unmatched b)."""
    n, m = len(a), len(b)
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    step = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == inf:
                continue
            if i < n and c + 2 < cost[i + 1][j]:
                cost[i + 1][j], step[i + 1][j] = c + 2, "a"
            if j < m and c + 2 < cost[i][j + 1]:
                cost[i][j + 1], step[i][j + 1] = c + 2, "b"
            if i < n and j < m:
                pa, pb = a[i], b[j]
                if pa == pb:
                    k = 0
                elif pa is not None and pb is not None and len(diff_bytes(pa, pb)) <= near:
                    k = 1
                else:
                    continue
                if c + k < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], step[i + 1][j + 1] = c + k, ("eq" if k == 0 else "near")
    pairs, ua, ub = [], [], []
    i, j = n, m
    while i or j:
        s = step[i][j]
        if s == "a":
            i -= 1
            ua.append(i)
        elif s == "b":
            j -= 1
            ub.append(j)
        else:
            i, j = i - 1, j - 1
            pairs.append((i, j, s))
    return pairs[::-1], ua[::-1], ub[::-1]


def pc_vs_tv(pc, tv, fps=None):
    pcc, tvc = changes(pc), changes(tv)
    if not any(p is None for _, p in tvc):
        # a TV log that never says "no packet" cannot show HDR10+ stopping: such changes are not compared
        pcc = [c for c in pcc if c[1] is not None]
    pairs, ua, ub = align([p for _, p in pcc], [p for _, p in tvc])
    lines, bad = [], 0
    for i in ua:
        k, p = pcc[i]
        lines.append(f"sent, not received: frame {pc[k][0]} (flip {pc[k][1]}) {hexs(p)}")
        bad += 1
    for j in ub:
        k, p = tvc[j]
        lines.append(f"received, not sent: TV entry {k} at {tv[k][0]} ns {hexs(p)}")
        bad += 1
    for i, j, kind in pairs:
        if kind == "near":
            a, b = pcc[i][1], tvc[j][1]
            lines.append(f"altered: frame {pc[pcc[i][0]][0]}: bytes {diff_bytes(a, b)}: sent {hexs(a)} "
                         f"received {hexs(b)}")
            bad += 1

    counters = all(tv[tvc[j][0]][1] is not None for _, j, _ in pairs)
    if counters:
        deltas = [tv[tvc[j][0]][1] - pc[pcc[i][0]][1] for i, j, _ in pairs]
        unit = "vsyncs (TV counter against flip sequence)"
        offsets = [d - statistics.median_low(deltas) for d in deltas] if deltas else []
    else:
        if fps:
            period = 1e9 / fps
        else:
            gaps = [pc[k + 1][2] - pc[k][2] for k in range(len(pc) - 1) if pc[k + 1][1] == pc[k][1] + 1]
            period = statistics.median(gaps) if gaps else None
        if pairs and not period:
            raise ValueError("no frame period: give --fps, or a PC dump with consecutive flips")
        deltas = [tv[tvc[j][0]][0] - pc[pcc[i][0]][2] for i, j, _ in pairs]
        base = statistics.median(deltas) if deltas else 0
        offsets = [round((d - base) / period) for d in deltas]
        unit = f"frames of {period / 1e6:.3f} ms (TV time against flip time)"
    hist = {}
    for o in offsets:
        hist[o] = hist.get(o, 0) + 1
    for (i, j, _), o in zip(pairs, offsets):
        if o:
            lines.append(f"change at frame {pc[pcc[i][0]][0]} (flip {pc[pcc[i][0]][1]}): {o:+d} against the others")
            bad += 1
    head = (f"pc vs tv: {len(pcc)} changes sent, {len(tvc)} received, {len(pairs)} matched "
            f"({sum(1 for p in pairs if p[2] == 'near')} altered), {len(ua)} missing, {len(ub)} extra; "
            f"offsets in {unit}: " + ", ".join(f"{k:+d}: {v}" for k, v in sorted(hist.items())))
    return head, lines, bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref")
    ap.add_argument("--ref-version", type=int, default=1, help="application version of --ref (JSON has none)")
    ap.add_argument("--pc")
    ap.add_argument("--tv")
    ap.add_argument("--fps", type=float, help="frame rate, where the PC dump cannot give the period")
    ap.add_argument("--keep-overlay", action="store_true",
                    help="compare the graphics overlay flag too (Kodi sets it while its GUI is shown)")
    a = ap.parse_args()
    try:
        ref = load_ref(a.ref, a.ref_version, a.keep_overlay) if a.ref else None
        pc = load_pc(a.pc, a.keep_overlay) if a.pc else None
        tv = load_tv(a.tv, a.keep_overlay) if a.tv else None
    except (OSError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    bad, done = 0, False
    try:
        if ref is not None and pc is not None:
            head, lines, n = ref_vs_pc(ref, pc)
            print(head, *lines, sep="\n")
            bad += n
            done = True
        if pc is not None and tv is not None:
            head, lines, n = pc_vs_tv(pc, tv, a.fps)
            print(head, *lines, sep="\n")
            bad += n
            done = True
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not done:
        print("error: give --ref and --pc, or --pc and --tv (or all three)", file=sys.stderr)
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
