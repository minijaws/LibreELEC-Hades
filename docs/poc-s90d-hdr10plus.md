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
| PC | Beelink SER7: Ryzen 7 7840HS, Radeon 780M (Phoenix, DCN 3.1.4); HDMI 2.1, DP 1.4, 2x USB4; two M.2 2280 slots; runs SteamOS now |
| AVR | Denon AVR-X3800H: HDR10 / HLG / DV / HDR10+ / Dynamic HDR passthrough on all six inputs; decodes TrueHD Atmos and DTS:X |
| TV | Samsung S90D with root shell access: HDR10+, no Dolby Vision |
| Cabling | PC -> X3800H HDMI in -> X3800H Monitor 1 out -> S90D eARC port |
| Build | Hades 1.1 (fork `minijaws/LibreELEC-Hades`), booted from USB for now; SteamOS stays on the internal disk (see Dual boot) |

Record whether the S90D is QD-OLED or WOLED (it depends on screen size)
in every result.

Whether the SER7's HDMI port is driven by the APU itself or through a
DisplayPort-to-HDMI converter is not stated by Beelink or by any review
found (one spec site even lists it as HDMI 2.0). Phase 0 settles it on
the machine; several results below depend on it.

## Why this design

- **Audio into the AVR, not through the TV.** Samsung TVs have long
  had no DTS over eARC; whether the S90D passes DTS-HD MA / DTS:X is
  not verified (newer Samsung models may). Either way the PC goes into
  the AVR, which passes the video on: no eARC handshake or lip-sync in
  the audio path. eARC stays for the TV's own apps.
- **Why HDR10+ and not plain HDR10:** DV shown as HDR10 carries only
  static metadata, so the TV maps with one curve for the whole film
  ("double tone mapping" if the PC also maps). HDR10+ lets the TV map
  each scene from metadata. The S90D's own dynamic mapping of HDR10
  (HDR Tone Mapping: Active) is the other competitor, see Phase 2.
- **Cheaper alternative to beat (Phase 2, option C):** the PC maps the
  picture to the panel's peak and tells the TV it fits, so the TV
  barely maps again. HDR10+ is only worth it if it is clearly better
  than this.
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
  eDP). Layout taken from CoreELEC's GPL Amlogic driver. On AMD it has
  been confirmed on one setup only: RX 7600 + JVC NZ9 over TMDS, where
  the packets of native HDR10+ films matched a licensed player byte for
  byte (10 packets compared). The `linux-9953` header says "Not run on a
  GPU" as written.
- DCN 3.1.4 sends its HDMI packets through the DCN 3.0 encoder functions
  (`enc3_stream_encoder_update_hdmi_info_packets`, upstream
  `dcn314_dio_stream_encoder.c`), which `linux-9953` patches: generic
  packet 7 on TMDS, 13 on FRL, GSP 6 on DisplayPort.
- Sent only when the display's EDID declares HDR10+ (any application
  version; v1 metadata never go to a v0 display), the HDR is PQ and the
  connector has the property. After 8 refused commits it falls back to
  HDR10 for that video.
- `videoscreen.hadesdvashdr10plus` ("Send it as HDR10+ where the display
  takes it", off by default): DV rebuilt (reshaping, P5 IPT; P8.1 is
  rebuilt too) and sent as HDR10+ with ST 2094-40 metadata measured on
  the GPU from the rebuilt picture: one window, average maxRGB plus 9
  percentiles, application version 1, profile A, no tone curve on
  purpose ("a curve would be a grade that nobody made"). Profile 7 with
  an enhancement layer also needs `videoscreen.hadesdvashdr10fel`
  ("Also with an enhancement layer"); without it P7 plays its HDR10 base
  layer, with no HDR10+. Not used while the picture is mapped to the
  display (`videoscreen.hadesdvhdr10map`).
- Scenes come from the RPU (L1 change or scene refresh flag), not from
  the picture. Measurement error vs. an ideal per-scene measurement:
  2.9 codes of 16 cd/m2 on average (RC4 figures); metadata estimated
  from L1 alone: 31.9.
- `kodi.log` reports per video whether HDR10+ goes out and why not, and
  every 240 frames the counts of frames, scenes and changes; not the
  payload of each frame. The player information shows "Dolby Vision
  rebuilt, sent as HDR10+ (metadata measured by Hades)" and, on its
  output page, the link (FRL lanes and rate, or TMDS clock) and format.
- Reported on, for HDR10+: Odyssey OLED G6 (RTX 4080 SUPER), JVC NZ9
  (RX 7600), Titan Noir Max through a Denon X4800H. Samsung S90H on
  Intel (issue #2, DV as HDR10 and HDR10+ at 2160p24). The X3900H
  report is native DV to an LG C1, not HDR10+. An X3800H appears in
  `kodi-9634` (an output kept on for its sound), never with HDR10+.

## Open questions this setup can answer

1. **Timing:** the docs say the drivers write the InfoFrame when the
   commit is made, not in step with the flip, so a change may reach the
   display a frame early or late (not verified). What the code does on
   DCN 3.x:
   - A change of `HDR10PLUS_VSIF` is a DC stream update of its own, in
     the connector loop of `amdgpu_dm_atomic_commit_tail()`; the flip is
     programmed after it, in `amdgpu_dm_commit_planes()`, which waits
     for the vblank before the flip's target.
   - The packet is written into the VPG with `immediate_update = true`
     (double buffered, "update at next block_update when
     block_update_lock == 0") and sent on line 2 of every frame. The
     flip latches at the VUPDATE of its target vblank.
   - Expected: the same frame in steady state (Kodi's non-blocking
     commit aimed at the next vblank), the packet one frame early when
     the commit straddles a vblank between the two updates. The packet
     is written only when the metadata change, so only scene cuts are
     exposed, and the first picture of a scene waits for its measurement
     until 4 ms before the vblank: the frames that matter are the ones
     committed closest to it. 4 ms is a tighter margin at 60p (16.7 ms)
     than at 24p (41.7 ms).
2. **Quality:** how a display maps scenes with these metadata vs. the
   same film as plain HDR10 has not been compared.
3. **AVR:** do per-frame HDR10+ InfoFrames pass through an X3800H
   unchanged?
4. **Phoenix (780M):** HDR10+ and DV rebuild performance and correctness.
5. **Tone curve:** would a Bezier curve fitted from the DV trims (L2/L8)
   for the S90D's peak look better, and does the S90D use a curve at all?
6. **FRL:** does amdgpu's HDMI FRL work on Phoenix through the X3800H,
   and does HDR10+ go out in its FRL slot (13, never tried)?

## Known issues to watch

- **4:2:2 at 4K50/60 HDR:** only DCN 3.1 and 3.1.5 set
  `no_native422_support` (upstream 7.3.5 `dcn31_resource.c`;
  `dcn314_resource.c` does not). Phoenix should send YCbCr 4:2:2 12-bit
  itself (`linux-9945`), so `videoscreen.hadeshdr10tunnel` should not
  engage: `HDR over the tunnel: not used for this video (...)` in
  `kodi.log` is then expected, not a fault. (how-it-works.md counts "the
  integrated graphics of the Ryzen 7000 series" among DCN 3.1/3.1.5;
  that holds for Rembrandt-R, Mendocino and Raphael, not Phoenix.)
  Record the link format from the player information; read from the
  source, not seen on hardware.
- **GPU clocks:** Phoenix has no power profiles;
  `<video><dvgpuforcehigh>true</dvgpuforcehigh></video>` holds the clock
  high while the DV engine draws (`kodi-9539`).
- **RGB range:** amdgpu sent full-range RGB while signalling limited to
  sinks without the QS bit (drm/amd #5796). Hades carries the upstream
  fix (5342f7d98a26) and `linux-9944` / `linux-9955`. The sink here is
  the X3800H's EDID: record its QS bit; crushed or washed-out blacks
  would be a regression.
- **HD audio passthrough on Linux** has had regressions (TrueHD / DTS-HD
  MA silent while AC3 / DTS core work; a 6700 XT report on amdgpu).
  Seek, pause and refresh-rate changes are where it breaks.
- **TV firmware:** keep auto-update off; an update may remove root.
- **Denon firmware:** update first; HDMI quirks have been fixed in it.

Not applicable to this path: the "L5 bug" (GUI missing in the bars,
reported on an 8845HS/780M) concerns native DV, whose L5 Hades handles
(`kodi-9216`); DV sent as HDR10+ is an ordinary HDR10 frame with the GUI
composited by Kodi and no L5. Nor OpenCL: Hades does not use it.

## Phase 0: recon

- [ ] Flash Hades 1.1 Generic x86_64 to USB; boot the SER7 from it.
- [ ] HDMI port: `ls /sys/class/drm/` with the TV on the HDMI port.
      `card*-HDMI-A-*` = driven as HDMI by the APU; `card*-DP-*` = a
      DisplayPort-to-HDMI converter, where HDR10+ goes out as a DP SDP
      that the converter must turn into an InfoFrame (many do not) and
      FRL is trained by the converter. Note `dmesg | grep -i -e pcon -e
      dongle`. Everything below assumes HDMI-A; if it is DP, test
      HDR10+ on that first.
- [ ] `cat /sys/module/amdgpu/parameters/dcfeaturemask` (for Phase 1b).
- [ ] X3800H: input "4K/8K Signal Format" = 8K Enhanced (or Enhanced);
      Video Conversion off; Resolution = Through; certified Ultra High
      Speed cables.
- [ ] S90D: Input Signal Plus on for the AVR's port; Game Mode and Auto
      Game Mode off on that input; input icon not "PC" (PC inputs may
      take the HDR10+ Gaming path). Note HDR Tone Mapping (Active /
      Static).
- [ ] Kodi audio: passthrough on; AC3, E-AC3, DTS, TrueHD, DTS-HD on;
      output device = the AMD HDMI port.
- [ ] Kodi video: Hades' defaults. Not the recipe of other forks' DV
      threads: DV that is rebuilt is decoded with VAAPI whatever DRM
      PRIME says (settings.md), and every changed setting is a variable.
      For the tests: `videoscreen.hadesdvashdr10plus` and
      `videoscreen.hadesdvashdr10fel` on.
- [ ] EDID through the AVR: `edid-decode /sys/class/drm/card*-HDMI-A-*/edid`.
      Need the HDR10+ block (OUI `90-84-8B`, note its application
      version), YCbCr 4:2:2, the QS bit, the HF-VSDB maximum FRL rate,
      and TrueHD / DTS-HD / E-AC3 in the audio blocks. Repeat with the
      PC straight into the TV and compare.
- [ ] TV root recon:
  - [ ] SoC, kernel version, loaded modules; `dmesg` while switching an
        HDMI input between SDR / HDR10 / HDR10+.
  - [ ] `dlogutil` during the same switches: the service that changes the
        picture mode parses the HDR10+ VSIF and may log it.
  - [ ] Which process reacts (its open device nodes during a switch);
        trace its syscalls: the read or ioctl that returns the packet is
        the place to log from.
  - [ ] `/proc`, `/sys`, debugfs: `infoframe`, `vsif`, `hdr`, `2094`,
        `hdr10p`; a frame or vsync counter of the video path.
  - [ ] The tone curves already found: where they are (file, service
        menu value, process memory, device node), and whether they can be
        read while HDR10 / HDR10+ plays and change with the scene.
  - [ ] Whether the service menu shows the incoming HDR format or
        metadata.
- [ ] Reference HDR10+ source for captures: Apple TV 4K, or an Amlogic
      box on CoreELEC (which also sends HDR10+ from GPL code).

## Phase 0.5: capture and compare tool

- [ ] TV side: a logger that writes each received HDR10+ VSIF (and DRM
      InfoFrame) with a timestamp and, where there is one, the TV's frame
      or vsync counter, one packet per line, hex payload. If the TV only
      logs changes, it shows cuts but cannot tell "one frame early" from
      "missing".
- [ ] TV side: a logger of the applied tone curve per scene, if Phase 0
      found it readable during playback.
- [ ] PC side: a per-frame dump from Hades (a debug log added in this
      fork): frame number, flip sequence and timestamp from the page-flip
      event, the HDR10+ payload in hex. `kodi.log` alone is not enough.
- [ ] Compare script: lines the TV log up with the PC dump and with
      `hdr10plus_tool extract` of the source file, on the scene cuts
      (the two clocks are not synced); reports byte differences, missing
      or altered packets, and frame offsets at cuts. Can be written
      before the TV side exists.
- [ ] Frame-exact fallback: a synthetic HDR10+ clip, one still picture,
      metadata switched between extremes at known frames, filmed at
      240 fps. The TV may smooth metadata over time, which blunts it.
- [ ] Use them for every result below.

## Phase 1: transport

Run each step direct (PC -> TV) and through the AVR; then the reference
source through the AVR. Direct OK + via AVR broken = the AVR.

- [ ] Native HDR10+ demo clip: TV shows HDR10+; player info says
      "HDR10+ (PQ)"; captured VSIF matches `hdr10plus_tool extract`.
- [ ] Timing: frame of each metadata change vs. frame of the scene cut
      (open question 1), at 24p and at 60p.
- [ ] `videoscreen.hadesdvashdr10plus` on (and `hadesdvashdr10fel` for
      P7); P5, P8.1 and P7 FEL samples; TV shows HDR10+; `kodi.log`
      counts sent / scenes / changes.
- [ ] 4K60 HDR: player info gives YCbCr 4:2:2 12-bit, and `kodi.log`
      says why the tunnel is not used (see Known issues).
- [ ] 780M: dropped or skipped frames at 24p and 60p, with and without
      menus shown; try `dvgpuforcehigh`.
- [ ] Audio on this build: TrueHD Atmos, DTS:X, DTS-HD MA 7.1, E-AC3
      Atmos, each shown on the X3800H front display; repeat after seek,
      pause and a refresh-rate change.
- [ ] Also check the X3800H Info screen for the incoming HDR format.

## Phase 1b: HDMI FRL (after Phase 1 on TMDS)

The 7.3 kernel has amdgpu FRL, off by default (`DC_FRL_MASK`, 0x400 in
`amdgpu.dcfeaturemask`); DCN 3.1.4 has one HPO FRL encoder. Hades does
not change the mask, so Phases 1 and 2 run on TMDS (600 MHz), as other
users do. FRL buys little for films (24p fits TMDS; 60p video is 4:2:0,
carried exactly by 4:2:2 12-bit) but gives 4K120 / 144 menus with VRR
and full GUI colour at 4K60 HDR. The DV tunnel refuses FRL
(`linux-9925`); DV sent as HDR10+ does not need it.

- [ ] Kernel line: the value read in Phase 0 OR 0x400
      (`dcfeaturemask` replaces the default, it does not add to it).
- [ ] Link trained: output page of the player information and `dmesg`,
      direct and through the AVR (its FRL rate on that input).
- [ ] Native HDR10+ clip at 4K60 over FRL: captured packets against
      `hdr10plus_tool extract` (packet 13 of the FRL encoder: the first
      test of that path).
- [ ] Menus at 4K120 -> video at 24p and 60p -> menus, and a hot-plug:
      the TMDS <-> FRL changes that hung an RTX 4080 SUPER (how-it-works,
      Graphics drivers).
- [ ] Compare with SteamOS on the same setup (Dual boot): its
      `dcfeaturemask`, link and 4K120 VRR through the X3800H.

## Phase 2: quality A/B

Same scenes, same TV picture mode (recorded with each result),
measured: the TV's applied curve where Phase 0 found it readable,
otherwise photographed or with a meter.

- [ ] A: DV sent as HDR10+ (Hades measured metadata).
- [ ] B1: DV shown as HDR10 (static metadata), HDR Tone Mapping Static.
- [ ] B2: the same with HDR Tone Mapping Active: Samsung's own per-scene
      mapping, the real competitor to A.
- [ ] C: DV mapped by Hades to the S90D (`videoscreen.hadesdvhdr10map`
      "To this display", `hadesdvhdr10peak` / `hadesdvhdr10gamut` /
      `hadesdvhdr10black` from measurements), HDR Tone Mapping Static.
- [ ] D (offline, the TV's own player): the file converted with
      `dovi_tool` (extract RPU) -> L1-to-HDR10+ JSON script ->
      `hdr10plus_tool inject`. This changes the metadata (L1-derived,
      which Hades measured far worse) as well as the transport. Loses
      FEL; P5 not usable this way.
- [ ] D' (transport only): Hades' own per-frame metadata from the PC
      dump, as JSON, injected into the base layer and played in the TV's
      player. Close only for P8.1 whose reshaping changes little.

If A is not clearly better than both B2 and C, stop and report that;
it is a result.

## Contingent work (only where the tests call for it)

- **Timing fix:** if metadata land a frame off at cuts, send the packet
  in the same DC update as the flip address, after the vblank wait
  (`bundle->stream_update` in `amdgpu_dm_commit_planes()`), instead of
  the connector loop. Not small: it touches the flip path, has to live
  with the DV tunnel commits, and that the DIG block update latches with
  the OTG lock rests on a DC comment ("assume otg master lock will
  unlock it"), not on documentation.
- **Performance:** if the 780M drops frames, profile the GPU path and
  contribute numbers.
- **Tone curve:** if the S90D does poorly with the statistics alone, an
  expert option for a Bezier curve fitted from L2/L8 for the panel's
  peak; needs measurements to be accepted upstream. First check from the
  TV's curve readout whether the S90D applies a curve at all.

Not in scope: changing how Hades measures metadata (its most worked-out
part).

## Dual boot with SteamOS

Hades is built for "the other system first in the boot order, Hades for
one start": `hades-bootnext again` sets `BootNext` from `BootCurrent`, so
Hades' own restarts (update, first start, Kodi's Reboot) come back to
Hades whatever is first. It never adds, removes or reorders boot
entries. Installed on an internal disk it does not see the other disks
or the other partitions of its own disk. Secure Boot stays off for both
(SteamOS does not support it).

- **Boot order:** SteamOS first. To Hades: the firmware's boot menu, or
  `sudo efibootmgr -n <Hades entry>` and a restart from SteamOS (from
  Gaming Mode it needs a sudoers rule; check that `/etc` survives
  SteamOS updates). SteamOS updates may put SteamOS first again, which
  this layout expects.
- **To SteamOS from Kodi:** not there yet. `hades-bootnext` knows only
  Windows (`\EFI\Microsoft\Boot\bootmgfw.efi`). A `steamos` target that
  matches SteamOS's loader (believed to be `\EFI\steamos\steamcl.efi`;
  check with `efibootmgr -v`) and a power-menu entry after `kodi-9066` /
  `kodi-9119` would do it: a small change in this fork.
- **Where Hades lives:**

  | Option | Strength | Weakness |
  | :--- | :--- | :--- |
  | USB (Phases 0 and 1) | No risk to SteamOS | Slower; a stick is less reliable over months |
  | Second NVMe (the SER7 has two M.2 2280 slots) | Hades' own installer, whole disk; a SteamOS reimage cannot touch it | Costs a drive |
  | Same NVMe as SteamOS | No hardware | Manual: shrink `/home` (ext4) offline from a live Linux, add a FAT System partition (1024 MB or more on Generic) and an ext4 Storage partition, `efibootmgr -c` for Hades' syslinux loader; two EFI system partitions on one disk; a SteamOS reimage wipes Hades too |

  Plan: USB through Phase 1, then the second NVMe.
- **For the tests:** both systems share one TV input and its picture
  settings; SteamOS may signal ALLM and switch the input to Game Mode.
  Record the picture mode with every result. SteamOS (Valve's kernel,
  gamescope) is a control on the same PC, AVR and TV: its EDID dump
  should match Hades', and see Phase 1b. It cannot send HDR10+.

## Reporting upstream

Issue on `hadesdvls/LibreELEC-Hades` with EDID, captures, timing table,
`kodi.log` and A/B results, only when ready. Before posting: an EDID
carries the TV's serial number; strip it. Worth reporting on their own:
Phoenix sending 4:2:2 itself (and the how-it-works.md line that counts
it with DCN 3.1/3.1.5), and HDR10+ over FRL.

## Later: SteamOS HDR10+ (out of scope now)

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

- Commit author is `minijaws <minijaws@users.noreply.github.com>`; amend
  to the real GitHub noreply address if wanted.
- Second NVMe for Hades, or the SteamOS disk.
