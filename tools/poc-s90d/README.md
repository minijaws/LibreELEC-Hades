# tools/poc-s90d

Tools of the S90D proof of concept ([docs/poc-s90d-hdr10plus.md](../../docs/poc-s90d-hdr10plus.md)),
Phase 0.5. Host only; nothing here goes into an image. Python 3.8 or later, NumPy, and FFmpeg with
libx265 (for the clip). No HDR10+ support is needed in x265: the metadata are written by
`hdr10plus.py`.

| File | What it does |
| --- | --- |
| `hdr10plus.py` | HDR10+ (ST 2094-40, one window): `hdr10plus_tool` JSON, the T.35 message of the SEI, the 27-byte InfoFrame payload as Hades makes it (a port of `MakeVsif()` of `xbmc/utils/HDR10PlusVsif.h`), SEI insertion and extraction in HEVC Annex B |
| `make_timing_clip.py` | a 4K PQ clip whose HDR10+ metadata switch between two states at known frames, with a marker and a frame counter in the picture, for filming at 240 fps |
| `compare_hdr10plus.py` | lines up the source (`hdr10plus_tool extract`), what Hades sent and what the TV received |
| `test_poc.py` | the host test: 29 checks, including the 27 bytes that Hades' own host test pins for FFmpeg's FATE metadata, the clip read back by FFmpeg's decoder frame by frame, the compare tool on logs with known faults, and a mutant for each piece of logic it protects |

## The timing clip

    ./make_timing_clip.py --out clip            # 3840x2160, 23.976p, 65 s, about 1.5 min to make
    ./make_timing_clip.py --out clip60 --fps 60000/1001

- LOW frames say the scene peaks at 1000 cd/m2, HIGH frames say 4000. On a panel of about 1000
  cd/m2 the patches of 600 to 1000 cd/m2 dim on HIGH, if the TV follows the metadata.
- The marker square (top left, 100 cd/m2 on HIGH frames) shows the frame the picture is at. The 12
  squares at the bottom are the frame number in binary, most significant bit first.
- The schedule: LOW lead-in, then alternating blocks, then single HIGH frames inside LOW
  (`schedule.tsv`). A TV that smooths metadata over time hides the single frames, not the blocks.
- `metadata.json` is the reference for `compare_hdr10plus.py --ref`.

## Log formats for `compare_hdr10plus.py`

Tab-separated. Lines starting with `#` and a header line are skipped. `-` means no HDR10+ packet. A
packet is the 27-byte payload, or the whole 31-byte InfoFrame (`81 01 1B`, checksum, payload), in
hex.

**PC (`--pc`), one line per flip.** This is what a debug log added to Hades should write.

    frame   flip_seq   flip_ns   packet

- `frame` is the source frame shown, counted in display order from the start of the stream, as
  `hdr10plus_tool extract` counts them.
- `flip_seq` and `flip_ns` are the `sequence` and the time (`tv_sec`, `tv_usec`) of the DRM
  page-flip event of the commit that showed it.
- `packet` is the blob given to `HDR10PLUS_VSIF` in that commit.

**TV (`--tv`), one line per received packet, or per change.**

    time_ns   counter   packet

- `counter` is a frame or vsync counter of the TV's video path, or `-` where there is none.
- A log that never writes `-` cannot show HDR10+ stopping, so those changes are not compared.

## What the reports mean

- **`ref vs pc`:** frame by frame, with the frame offset that matches best.
  - Differences are expected where Hades does something on purpose: it holds the last packet over
    fewer than 12 frames without metadata, stops at 12, and sends nothing of application version 1
    to a display of version 0.
  - The graphics overlay flag (set while Kodi's GUI is over the video) is left out of every
    comparison unless `--keep-overlay` is given.
- **`pc vs tv`:**
  - The changes of each log are matched in order, as equal or altered (3 bytes or fewer
    different). The report lists what was not received, what was received but not sent, and what
    was altered.
  - Each matched change gets its offset against the median offset of all of them, in vsyncs (with
    a TV counter) or in frames (with the times).
  - The two clocks are not shared, so a delay that is the same for every change does not show; a
    change a frame apart from the others does. The filmed clip gives the absolute delay.

Exit status: 0 when nothing differs, 1 when something does, 2 on unreadable input.

## Running the test

    python3 -I tools/poc-s90d/test_poc.py
