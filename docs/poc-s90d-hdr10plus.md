# Proof of concept: Dolby Vision as HDR10+ on a Samsung S90D

Working plan for testing (and only where the tests call for it,
improving) "Dolby Vision sent as HDR10+" on a setup that no one has
reported on yet. Not part of the Hades documentation. Written so that a
new session can pick up the work from this file alone.

## Goal

Play local Dolby Vision files (P5, P7 FEL, P8.1) on a TV without Dolby
Vision, with the DV picture rebuilt on the PC and sent as HDR10+ so that
the TV maps each scene itself, while TrueHD Atmos and DTS:X go untouched
to the AV receiver.

## Setup

| Part | What |
| :--- | :--- |
| PC | Beelink SER7: Ryzen 7 7840HS, Radeon 780M (Phoenix, DCN 3.1.4); runs SteamOS now |
| AVR | Denon AVR-X3800H: HDR10 / HLG / DV / HDR10+ / Dynamic HDR passthrough on all six inputs; decodes TrueHD Atmos and DTS:X |
| TV | Samsung S90D with shell access: HDR10+, no Dolby Vision, no DTS of any kind |
| Cabling | PC -> X3800H HDMI in -> X3800H Monitor 1 out -> S90D eARC port |
| Build | Hades 1.1 (fork `minijaws/LibreELEC-Hades`), booted from USB; SteamOS stays on the internal disk |

Record whether the S90D is QD-OLED or WOLED (it depends on screen size)
in every result.

## Why this design

- **Samsung has no DTS.** It does not pass DTS-HD MA / DTS:X over eARC
  at all, so audio cannot go PC -> TV -> eARC -> AVR. The PC goes into
  the AVR, which passes the video on. eARC stays for the TV's own apps.
  TrueHD Atmos would pass over Samsung eARC; DTS will not.
- **Why HDR10+ and not plain HDR10:** DV shown as HDR10 carries only
  static metadata, so the TV maps with one curve for the whole film
  ("double tone mapping" if the PC also maps). HDR10+ lets the TV map
  each scene from metadata.
- **Cheaper alternative to beat (Phase 2, option C):** the PC maps the
  picture to the panel's peak and sends MaxCLL at that peak, so the TV
  barely maps again. HDR10+ is only worth it if it is clearly better than
  this.
- **Why Hades, not CroqueMr:** CroqueMr's `libreelec-x86-DV` (AMD iGPUs
  incl. Ryzen 7040 supported, R1.0.0 Beta5) has DV processing and HDR10
  output modes ("Reference Basic", "Reference Expert" with a TV profile)
  but states "HDR10+ not supported". It is the fallback if Hades stalls
  (single maintainer). The original `CroqueMr/intel-dv-libreelec` is an
  Intel-only RC and superseded.

## What Hades 1.1 already has (see docs/how-it-works.md)

- `HDR10PLUS_VSIF` connector property: per-frame HDR10+ InfoFrame sent
  in the commit of each flip, never its own commit or mode change
  (`linux-9952` DRM, `linux-9953` amdgpu on DCN over HDMI and DP, not
  eDP). On an RX 7600 + JVC NZ9 the packets matched a licensed player
  byte for byte. Layout taken from CoreELEC's GPL Amlogic driver.
- Sent only when the display's EDID declares HDR10+ (any application
  version; v1 metadata never go to a v0 display), the HDR is PQ and the
  connector has the property. After 8 refused commits it falls back to
  HDR10 for that video.
- `videoscreen.hadesdvashdr10plus` ("Send it as HDR10+ where the display
  takes it", off by default): DV rebuilt (reshaping, FEL, P5 IPT; P8.1 is
  rebuilt too) and sent as HDR10+ with ST 2094-40 metadata measured on
  the GPU from the rebuilt picture: one window, average maxRGB plus 9
  percentiles, application version 1, profile A, no tone curve on
  purpose ("a curve would be a grade that nobody made").
- Scenes come from the RPU (L1 change or scene refresh flag), not from
  the picture. Measurement error vs. an ideal per-scene measurement:
  2.9 codes of 16 cd/m2 on average (RC4 figures).
- `kodi.log` reports per video whether HDR10+ goes out and why not, and
  every 240 frames the counts of frames, scenes and changes. The player
  information shows "Dolby Vision rebuilt, sent as HDR10+ (metadata
  measured by Hades)".
- Reported on: Samsung S90H (Intel), Odyssey G6 (NVIDIA), JVC NZ9
  (RX 7600). Through Denon receivers: X3900H, X4800H (not X3800H).

## Open questions this setup can answer

1. **Timing:** the docs say the drivers write the InfoFrame when the
   commit is made, not in step with the flip, so a change may reach the
   display a frame early or late (not verified).
2. **Quality:** how a display maps scenes with these metadata vs. the
   same film as plain HDR10 has not been compared.
3. **AVR:** do per-frame HDR10+ InfoFrames pass through an X3800H
   unchanged?
4. **Phoenix (780M):** HDR10+ and DV rebuild performance and correctness.
5. **Tone curve:** would a Bezier curve fitted from the DV trims (L2/L8)
   for the S90D's peak look better, and does the S90D use a curve at all?

## Known issues to watch

- **780M:** GUI missing in the black bars during DV playback was reported
  on an 8845HS/780M ("L5 bug", fixed in dangerouslaser's fork; check
  whether Hades has it).
- **amdgpu range:** on kernel 7.2, amdgpu sent full-range RGB while
  signalling limited range (drm/amd #5796, fixed upstream as
  5342f7d98a26). Check for crushed or washed-out blacks.
- **No OpenCL:** an RX 7600 report on CroqueMr's builds showed no DV and
  no HDR10 conversion without an AMD OpenCL driver. Unknown whether it
  applies to iGPUs or to Hades.
- **Ryzen 7000 iGPU display (DCN 3.1.x) cannot send 4:2:2 itself:**
  at 4K50/60 HDR, Hades packs 4:2:2 12-bit through its tunnel
  (`videoscreen.hadeshdr10tunnel`). Test 24p and 60p separately.
- **GPU clocks:** Phoenix has no power profiles;
  `<video><dvgpuforcehigh>true</dvgpuforcehigh></video>` holds the clock
  high while the DV engine draws.
- **HD audio passthrough on Linux** has had regressions (TrueHD / DTS-HD
  MA silent while AC3 / DTS core work; a 6700 XT report on amdgpu).
  Seek, pause and refresh-rate changes are where it breaks.
- **TV firmware:** keep auto-update off; an update may remove shell access.
- **Denon firmware:** update first; HDMI quirks have been fixed in it.

## Phase 0: recon

- [ ] Flash Hades 1.1 Generic x86_64 to USB; boot the SER7 from it.
- [ ] X3800H: input "4K/8K Signal Format" = 8K Enhanced (or Enhanced);
      Video Conversion off; Resolution = Through; certified Ultra High
      Speed cables.
- [ ] S90D: Input Signal Plus on for the AVR's port; Game Mode off;
      input icon not "PC" (PC inputs may take the HDR10+ Gaming path).
- [ ] Kodi audio: passthrough on; AC3, E-AC3, DTS, TrueHD, DTS-HD on;
      output device = the AMD HDMI port.
- [ ] Kodi video (as recommended in the DV threads): VAAPI on, DRM PRIME
      off, EGL render method, Direct-to-Plane off.
- [ ] EDID through the AVR: `edid-decode /sys/class/drm/card*-HDMI-A-*/edid`.
      Need the HDR10+ block (OUI `90-84-8B`, note its application
      version) and TrueHD / DTS-HD / E-AC3 in the audio blocks. Repeat
      with the PC straight into the TV and compare.
- [ ] TV shell recon: kernel version; `dmesg` while switching an HDMI
      input between SDR / HDR10 / HDR10+; search `/proc`, `/sys` and
      debugfs for `infoframe`, `vsif`, `hdr`, `2094`, `hdr10p`. Goal: a
      per-frame log of the received HDR10+ VSIF (and DRM InfoFrame).
- [ ] Reference HDR10+ source for captures: Apple TV 4K, or an Amlogic
      box on CoreELEC (which also sends HDR10+ from GPL code).

## Phase 0.5: capture and compare tool

- [ ] TV side: a logger that writes each received HDR10+ VSIF (and DRM
      InfoFrame) with a frame counter or timestamp, in a simple text
      format (one packet per line, hex payload). Depends on Phase 0.
- [ ] PC side: a script that lines the TV log up with what Hades sent
      (`kodi.log`, or a debug log added in this fork) and with
      `hdr10plus_tool extract` of the source file; reports byte
      differences, missing or altered packets, and frame offsets at scene
      cuts. Can be written before the TV side exists.
- [ ] Use it for every result below.

## Phase 1: transport

Run each step direct (PC -> TV) and through the AVR; then the reference
source through the AVR. Direct OK + via AVR broken = the AVR.

- [ ] Native HDR10+ demo clip: TV shows HDR10+; player info says
      "HDR10+ (PQ)"; captured VSIF matches `hdr10plus_tool extract`.
- [ ] Timing: frame of each metadata change vs. frame of the scene cut
      (open question 1).
- [ ] `videoscreen.hadesdvashdr10plus` on; P5, P8.1 and P7 FEL samples;
      TV shows HDR10+; `kodi.log` counts sent / scenes / changes.
- [ ] 780M: dropped or skipped frames at 24p and 60p, with and without
      menus shown; try `dvgpuforcehigh`.
- [ ] Audio on this build: TrueHD Atmos, DTS:X, DTS-HD MA 7.1, E-AC3
      Atmos, each shown on the X3800H front display; repeat after seek,
      pause and a refresh-rate change.
- [ ] Also check the X3800H Info screen for the incoming HDR format.

## Phase 2: quality A/B

Same scenes, same TV picture mode, photographed or (better) measured:

- [ ] A: DV sent as HDR10+ (Hades measured metadata).
- [ ] B: DV shown as HDR10 (static metadata).
- [ ] C: DV mapped by Hades to the S90D's peak, MaxCLL at the panel peak.
- [ ] D (offline, isolates the metadata from the transport): the file
      converted with `dovi_tool` (extract RPU) -> L1-to-HDR10+ JSON script
      -> `hdr10plus_tool inject`, played in the TV's own player. Loses
      FEL; P5 not usable this way.

If A is not clearly better than C, stop and report that; it is a result.

## Contingent work (only where the tests call for it)

- **Timing fix:** if metadata land a frame off at cuts, make amdgpu
  latch the VSIF with the flip (small change to `linux-9953`).
- **Performance:** if the 780M drops frames, profile the GPU path and
  contribute numbers.
- **Tone curve:** if the S90D does poorly with the statistics alone, an
  expert option for a Bezier curve fitted from L2/L8 for the panel's
  peak; needs measurements to be accepted upstream. First check from the TV shell
  whether the S90D applies a curve at all.
- **TV shell:** see whether the TV exposes its tone-mapping state, to
  compare A, C and D objectively.

Not in scope: changing how Hades measures metadata (its most worked-out
part).

## Reporting upstream

Issue on `hadesdvls/LibreELEC-Hades` with EDID, captures, timing table,
`kodi.log` and A/B results, only when ready. Before posting: an EDID
carries the TV's serial number; strip it.

## Later: SteamOS (out of scope now)

SteamOS needs, in this order of difficulty: a dynamic-metadata DRM uAPI
upstream (`HDR_OUTPUT_METADATA` is static only, though its struct was
made with a type field for later kinds), Valve's kernel carrying it (the
read-only A/B image drops local patches), a Wayland color-management
extension for per-frame metadata, and gamescope passing it to the
connector with each flip. Hades' `HDR10PLUS_VSIF` property and its
metadata measurement are the parts that would carry over; keep any new
code as a separable library.

## Hardware notes (from the discussion)

- An Amlogic box on CoreELEC converts DV with Dolby's licensed hardware
  engine and is the most mature option for local files, but it is not
  hackable; which chips handle FEL changes, so check current CoreELEC
  threads. Useful as a reference HDR10+ source either way.
- Neither LibreELEC nor most Amlogic boxes replace the Nvidia Shield for
  streaming apps (no Widevine L1 on Linux: no 4K HDR Netflix etc.). Keep
  the Shield or the TV's apps for streaming.

## Open decisions

- Push this branch?
- Commit author is `minijaws <minijaws@users.noreply.github.com>`; amend
  to the real GitHub noreply address if wanted.
