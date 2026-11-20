#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 LibreELEC Hades
"""Host test of tools/poc-s90d: the HDR10+ library against Hades' InfoFrame and FFmpeg's decoder,
the timing clip read back by FFmpeg, and the compare tool on logs with known faults. Each check has
a mutant (an edit of the code it protects) that must make it fail.

Usage: test_poc.py   (exit 0 when every check passed; FFmpeg checks are skipped without ffmpeg)
"""

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_hdr10plus as cmp  # noqa: E402
import hdr10plus as h  # noqa: E402

FAILS = 0
CHECKS = 0


def check(ok, what):
    global FAILS, CHECKS
    CHECKS += 1
    print(("ok:   " if ok else "FAIL: ") + what)
    if not ok:
        FAILS += 1


# The metadata of FFmpeg's FATE test hevc-hdr10-plus-metadata and the 27 bytes that Hades' host test
# (tools/regress/dvbridge/tests/meta/hdr10plus_vsif_test.cpp) pins for them
FATE = h.Metadata(application_version=1, targeted_max_luminance=400, maxscl=(3340, 2870, 2720),
                  average_maxrgb=510, percentiles=(30, 2940, 255, 70, 1340, 1600, 1850, 1950, 2940),
                  fraction_bright_pixels=1, anchors=(102, 205, 307, 410, 512, 614, 717, 819, 922),
                  tone_mapping=True)
FATE_VSIF = bytes([0x8B, 0x84, 0x90, 0x5A, 0x03, 0x00, 0x12, 0xFF, 0x00, 0x08, 0x0A, 0x0C, 0x0C, 0x12,
                   0x90, 0x00, 0x00, 0x1A, 0x33, 0x4D, 0x67, 0x80, 0x9A, 0xB3, 0xCD, 0xE7, 0x40])


def library():
    p, why = h.vsif(FATE)
    check(p == FATE_VSIF and why is None, "FATE metadata: Hades' 27 bytes")
    v0 = dataclasses.replace(FATE, application_version=0, percentages=h.PERCENTAGES_10,
                      percentiles=(30, 2940, 2955, 2960, 3000, 3100, 3200, 3300, 3400, 3500))
    p0, _ = h.vsif(v0)
    check(p0 is not None and p0[7] == h._coded_maxrgb(2955) and p0[13] == h._coded_maxrgb(3500),
          "version 0, ten entries: the third coded as a percentile, 98 % left out")
    check(h.vsif(dataclasses.replace(FATE, application_version=2))[0] is None, "version 2: no InfoFrame")
    odd = dataclasses.replace(FATE, percentages=(1, 5, 10, 25, 50, 75, 90, 95, 98))
    check(h.vsif(odd)[0] is None, "a distribution of other percentages: no InfoFrame")
    ten = dataclasses.replace(v0, percentages=(1, 5, 10, 25, 50, 75, 90, 95, 97, 99))
    check(h.vsif(ten)[0] is None, "ten entries without 98 % before 99 %: no InfoFrame")
    shapes = [FATE, v0, dataclasses.replace(FATE, tone_mapping=False, anchors=()),
              dataclasses.replace(FATE, maxscl=(100000, 0, 99999), targeted_max_luminance=10000)]
    check(all(h.parse_t35(h.t35_payload(m)) == m for m in shapes), "T.35 message: written and read back, 4 shapes")
    frame = bytes([0x81, 0x01, 0x1B, 0]) + FATE_VSIF
    frame = frame[:3] + bytes([(-sum(frame)) & 0xFF]) + frame[4:]
    check(h.vsif_from_hex(frame.hex()) == FATE_VSIF, "a whole InfoFrame: its payload")
    try:
        h.vsif_from_hex((frame[:3] + bytes([frame[3] ^ 1]) + frame[4:]).hex())
        check(False, "a bad checksum is refused")
    except ValueError:
        check(True, "a bad checksum is refused")
    nal = h.sei_nal(bytes([0, 0, 0, 0, 1, 0, 0, 3]))
    check(h.sei_t35_messages(nal) == [bytes([0, 0, 0, 0, 1, 0, 0, 3])], "emulation prevention: escaped and back")


def ffmpeg_checks(tmp):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("skip: no ffmpeg")
        return
    out = os.path.join(tmp, "clip")
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "make_timing_clip.py"), "--out", out,
                    "--size", "320x180", "--fps", "24", "--lead", "5", "--block", "4", "--blocks", "3",
                    "--pulses", "2", "--pulse-gap", "3"], check=True, capture_output=True)
    with open(os.path.join(out, "schedule.tsv")) as f:
        states = [line.split("\t")[1] for line in f.read().splitlines()[1:]]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_frames", "-of", "json", os.path.join(out, "clip.mkv")],
                           check=True, capture_output=True, text=True)
    frames = json.loads(probe.stdout)["frames"]
    got = []
    for fr in sorted(frames, key=lambda x: x.get("pts", 0)):
        sd = [s for s in fr.get("side_data_list", []) if "maxscl" in s]
        got.append(sd[0]["maxscl"] if sd else None)
    check(all(fr.get("color_transfer") == "smpte2084" and fr.get("color_primaries") == "bt2020" for fr in frames),
          "timing clip: every frame PQ and BT.2020")
    want = ["40000/100000" if s == "HIGH" else "10000/100000" for s in states]
    check(len(got) == len(states) and all(g is not None and g.startswith(w.split("/")[0]) for g, w in zip(got, want)),
          f"timing clip: FFmpeg reads the scheduled metadata in every one of {len(states)} frames")
    ref = h.load_json(os.path.join(out, "metadata.json"))
    with open(os.path.join(out, "clip.hevc"), "rb") as f:
        check(h.extract_sei(f.read()) == ref, "timing clip: its SEI = metadata.json")


def write(path, rows):
    with open(path, "w") as f:
        f.write("\n".join("\t".join(str(c) for c in r) for r in rows) + "\n")


def compare_checks(tmp):
    frames = [h.Metadata(maxscl=(v, v, v), average_maxrgb=v // 10,
                         percentiles=(1, 2, 3, 4, 5, 6, 7, 8, v)) for v in
              [10000] * 10 + [40000] * 10 + [20000] * 10 + [30000] * 10]
    ref_path = os.path.join(tmp, "ref.json")
    h.dump_json(frames, ref_path)
    ref = cmp.load_ref(ref_path, 1, False)
    period = 41_708_333
    pc_rows = [(i, 1000 + i, 5_000_000_000 + i * period, ref[i].hex()) for i in range(len(ref))]
    pc = [(f, s, t, bytes.fromhex(p)) for f, s, t, p in pc_rows]

    _, _, bad = cmp.ref_vs_pc(ref, pc)
    check(bad == 0, "ref vs pc: the same packets: nothing differs")
    one = list(pc)
    b = bytearray(one[15][3])
    b[13] ^= 1
    one[15] = one[15][:3] + (bytes(b),)
    _, lines, bad = cmp.ref_vs_pc(ref, one)
    check(bad == 1 and "frame 15: bytes [13]" in lines[0], "ref vs pc: one byte of one frame: that frame and byte")
    shifted = [(f, s, t, ref[f - 1] if f else ref[0]) for f, s, t, _ in pc]
    head, _, _ = cmp.ref_vs_pc(ref, shifted)
    check("best frame offset -1" in head, "ref vs pc: sent a frame late: best offset -1")

    clock = 123_456_789_000
    tv = [(t + clock + 7_000_000, None, p) for _, _, t, p in pc]
    _, _, bad = cmp.pc_vs_tv(pc, tv)
    check(bad == 0, "pc vs tv: every frame logged, other clock: nothing differs")
    tv_changes = [x for k, x in enumerate(tv) if k == 0 or x[2] != tv[k - 1][2]]
    _, _, bad = cmp.pc_vs_tv(pc, tv_changes)
    check(bad == 0, "pc vs tv: only changes logged: nothing differs")
    late = [x if k != 2 else (x[0] + period, x[1], x[2]) for k, x in enumerate(tv_changes)]
    head, lines, bad = cmp.pc_vs_tv(pc, late)
    check(bad == 1 and "change at frame 20" in lines[0] and "+1" in lines[0],
          "pc vs tv: one change a frame late: that change, +1")
    missing = [x for k, x in enumerate(tv_changes) if k != 3]
    _, lines, bad = cmp.pc_vs_tv(pc, missing)
    check(any("sent, not received: frame 30" in s for s in lines), "pc vs tv: a change not received: named")
    alt = [x if k != 1 else (x[0], x[1], x[2][:20] + bytes([x[2][20] ^ 4]) + x[2][21:])
           for k, x in enumerate(tv_changes)]
    _, lines, _ = cmp.pc_vs_tv(pc, alt)
    check(any(s.startswith("altered: frame 10: bytes [20]") for s in lines), "pc vs tv: an altered byte: named")
    counted = [(0, 7000 + pc[k][1] + (1 if k == 20 else 0), p) for k, (_, _, p) in
               zip([0, 10, 20, 30], tv_changes)]
    _, lines, bad = cmp.pc_vs_tv(pc, counted)
    check(bad == 1 and "change at frame 20" in lines[0], "pc vs tv: TV counter, one change a vsync late: that change")

    pc_path, tv_path = os.path.join(tmp, "pc.tsv"), os.path.join(tmp, "tv.tsv")
    write(pc_path, [("frame", "flip_seq", "flip_ns", "packet")] + pc_rows)
    full = []
    for t, c, p in tv_changes:
        frame = bytes([0x81, 0x01, 0x1B, 0]) + p
        full.append((t, "-", (frame[:3] + bytes([(-sum(frame)) & 0xFF]) + frame[4:]).hex()))
    write(tv_path, full)
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "compare_hdr10plus.py"), "--ref", ref_path,
                        "--pc", pc_path, "--tv", tv_path], capture_output=True, text=True)
    check(r.returncode == 0, "the command, from files (TV logs whole InfoFrames): exit 0")


MUTANTS = [
    ("hdr10plus.py", "return min(255, (v + 8) >> 4)", "return min(255, v >> 4)"),
    ("hdr10plus.py", "if (i == 2 and m.application_version != 0)", "if (i == 2)"),
    ("hdr10plus.py", "        if zeros >= 2 and b <= 3:\n            out.append(3)",
     "        if False:\n            out.append(3)"),
    ("hdr10plus.py", "if m.percentages[entry] != 98:", "if False:"),
    ("compare_hdr10plus.py", "offsets = [round((d - base) / period) for d in deltas]",
     "offsets = [0 for d in deltas]"),
    ("compare_hdr10plus.py", "elif pa is not None and pb is not None and len(diff_bytes(pa, pb)) <= near:",
     "elif False:"),
]


def mutants():
    for name, old, new in [(None, None, None)] + MUTANTS:
        with tempfile.TemporaryDirectory() as d:
            for f in ("hdr10plus.py", "compare_hdr10plus.py", "make_timing_clip.py", "test_poc.py"):
                shutil.copy(os.path.join(HERE, f), d)
            if name is None:
                r = subprocess.run([sys.executable, "-I", os.path.join(d, "test_poc.py"), "--no-mutants",
                                    "--no-ffmpeg"], capture_output=True, text=True)
                check(r.returncode == 0, "control: the unchanged copy passes (else no mutant proves anything)")
                continue
            path = os.path.join(d, name)
            with open(path) as f:
                text = f.read()
            if text.count(old) != 1:
                check(False, f"mutant of {name}: its text is not in the code once ({old[:40]!r})")
                continue
            with open(path, "w") as f:
                f.write(text.replace(old, new))
            r = subprocess.run([sys.executable, "-I", os.path.join(d, "test_poc.py"), "--no-mutants", "--no-ffmpeg"],
                               capture_output=True, text=True)
            check(r.returncode != 0, f"mutant of {name} fails the test: {new.strip()[:50]!r}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        library()
        if "--no-ffmpeg" not in sys.argv:
            ffmpeg_checks(tmp)
        compare_checks(tmp)
    if "--no-mutants" not in sys.argv:
        mutants()
    print(f"{CHECKS - FAILS} of {CHECKS} checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
