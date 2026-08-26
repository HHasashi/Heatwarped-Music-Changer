# Heatwarped Music Patcher

A custom music patcher for **Heatwarped**.

The patcher lets you replace any of the 8 stock Telefon songs, add new songs without replacing anything, and optionally unlock the complete Telefon setlist in every music playlist used by the game.

You can replace only the songs you want while leaving every other stock song untouched.

---

## Features

- Replace any of the 8 stock Telefon songs individually
- Keep every stock song you don't want to replace
- Add extra custom songs without replacing stock music
- Read Artist / Title from audio metadata when enabled
- Smart metadata fallback when only Artist or Title is available
- Avoid duplicated display names such as `Hangar 18 - Hangar 18`
- Automatically remove common filename tags such as `Remaster`, `Remastered`, `Remix` and `Remixed`
- LUFS, peak or no volume normalization
- `stock` or `full` playlist mode for Drift, FreeRoam, MainMenu and Racing - allowing for those game modes to access any song and custom song
- Automatically convert supported audio files to the format required by the game
- Offer to download missing FFmpeg / oggvorbis2fsb5 tools on first launch

---

# Supported audio formats

You can use:

- `.mp3`
- `.wav`
- `.flac`
- `.ogg`
- `.oga`
- `.m4a`
- `.aac`
- `.opus`
- `.wma`
- `.aiff`
- `.aif`

You don't need to convert the songs yourself. The patcher converts them to 48 kHz stereo Vorbis automatically.

If you use the compiled release, **Python is not required** on the player PC.

[Python 3](https://www.python.org/) is only required if you want to run or build the Python version yourself.

[FFmpeg](https://www.gyan.dev/ffmpeg/builds/) and [oggvorbis2fsb5](https://github.com/uyjulian/oggvorbis2fsb5/releases/latest/) are required by the patcher. If one of them is missing from the `tools` folder, the patcher will ask if you want to download the missing tool automatically.

---

# Quick setup

## 1. Put the original game files in `input`

You need:

```text
Master.bank
resources.assets
sharedassets0.assets
```

Original locations:

```text
Master.bank
.\Heatwarped Demo\Heatwarped_Data\StreamingAssets

resources.assets
.\Heatwarped Demo\Heatwarped_Data

sharedassets0.assets
.\Heatwarped Demo\Heatwarped_Data
```

---

## 2. Put your music in `tracks`

The recommended filename format is:

```text
NN - Artist - Title.extension
```

Example:

```text
01 - Avenged Sevenfold - Blinded in Chains.mp3
```

But this format is **recommended, not required** anymore.

A supported audio file will still be accepted if its filename is incomplete, unnumbered or completely weird.

Examples:

```text
Megadeth - Hangar 18.mp3
Hangar 18.mp3
dsfgwegewg.mp3
```

Unnumbered files are added as custom songs after the numbered custom tracks.

---

## 3. Run the patcher

The easiest way is to use the compiled EXE from the **Releases** section.

If you use the Python version, you can launch it with `RUN_PATCHER.bat` or:

```text
py -3 heatwarped_patcher.py
```

If FFmpeg or oggvorbis2fsb5 is missing, the patcher will ask:

```text
Download missing tools now? (y/n):
```

Choosing `y` downloads only the missing tools and continues.

The patcher will create:

```text
output\Heatwarped_Data
```

---

## 4. Install

Copy the generated:

```text
Heatwarped_Data
```

folder to the root folder of the game.

Merge / replace the files when Windows asks.

---

# Detailed track behavior

## 1. Replacing the 8 stock songs — 01 to 08

Numbers `01` to `08` correspond directly to the 8 original Telefon songs.

```text
01 = carbon          | event:/Music/Carbon
02 = knifegirl       | event:/Music/knifegirl
03 = liberation      | event:/Music/Liberation
04 = midnight_stage  | event:/Music/MidnightStage
05 = sirens          | event:/Music/Sirens
06 = black           | event:/Music/Black
07 = nightworld      | event:/Music/nightworld
08 = to_the_top      | event:/Music/ToTheTop
```

A stock song is replaced **only if a file using its number exists in the `tracks` folder**.

For example, if you have:

```text
01
02
03
04
06
07
08
```

but no `05`, then:

```text
Sirens remains unchanged.
```

You don't need to provide all 8 songs.

If you only want to replace Carbon and Black:

```text
01 - Artist - Song.mp3
06 - Artist - Song.mp3
```

The other 6 stock songs stay untouched.

If your setlist starts at `09`, or only contains unnumbered files, then **none of the 8 stock songs are replaced**.

---

## 2. Duplicate numbers are allowed

Duplicate numbers no longer stop the patcher.

### Duplicate `01` to `08`

The first file for that number replaces the stock song.

Any additional file using the same number becomes a custom song instead.

Example:

```text
05 - Artist A - Song A.mp3
05 - Artist B - Song B.mp3
```

Result:

```text
Song A -> replaces stock slot 05
Song B -> added as a custom song
```

If multiple files share the same stock number, filenames are handled in alphabetical order, so the first one alphabetically is the replacement.

Duplicate `01–08` files that become custom songs are placed **before normal `09–99` custom songs**.

### Duplicate `09` to `99`

All of them are kept.

Example:

```text
09 - Artist A - Song A.mp3
09 - Artist B - Song B.mp3
10 - Artist C - Song C.mp3
```

All 3 songs are added as customs.

Tracks are sorted by number, then alphabetically by filename when the number is the same.

---

## 3. Adding custom music — 09 to 99

Starting from `09`, numbers do not correspond to existing game songs anymore.

They are only **order labels** for custom music.

Example:

```text
09 - Artist - Song A.mp3
10 - Artist - Song B.mp3
12 - Artist - Song C.mp3
```

The patcher creates 3 consecutive custom songs.

There is no empty slot for missing `11`.

You can skip as many numbers as you want:

```text
10 - Artist - Song A.mp3
45 - Artist - Song B.mp3
99 - Artist - Song C.mp3
```

still creates 3 consecutive customs in that order.

The visible numbers are only used to organize the input setlist. The patcher compacts custom songs into consecutive internal slots automatically.

---

## 4. Unnumbered / badly named files

A supported audio file is no longer skipped just because its filename doesn't follow a convention.

Unnumbered files are added **after all numbered custom songs**, alphabetically by filename.

Examples:

```text
Megadeth - Hangar 18.mp3
Fade To Black.flac
whatever this filename is.mp3
```

A simple filename such as:

```text
Megadeth - Hangar 18.mp3
```

can be interpreted as:

```text
Artist: Megadeth
Title: Hangar 18
```

A single-field filename such as:

```text
Hangar 18.mp3
```

becomes:

```text
Artist: [empty]
Title: Hangar 18
```

If an unnumbered filename has too many ambiguous ` - ` sections, the patcher does not try to guess an artist.

Example:

```text
dsf - asd gwegsd - weg - wsegseg - qweqwe - gsdg.mp3
```

Without usable metadata it will simply be accepted as a custom song with no artist and the cleaned filename as its title.

Invalid ordering numbers such as `00` or numbers outside the supported `01–99` input range do not crash the patcher. They are treated as unnumbered custom tracks instead.

---

## 5. Remaster / Remix filename cleanup

When the filename is used for Artist / Title, common remaster/remix fields are removed automatically.

For example:

```text
Metallica - Remastered - Fade To Black.flac
```

becomes:

```text
Artist: Metallica
Title: Fade To Black
```

This also covers fields containing words such as:

```text
Remaster
Remastered
2016 Remaster
Remix
Remixed
```

---

# Artist / Title metadata

Metadata reading is controlled by:

```json
"fetch_metadata": true
```

When enabled, the patcher asks FFmpeg for the audio file's `ARTIST` and `TITLE` tags.

## Complete metadata

If both tags exist, metadata has priority over the filename.

For example, even if the file is named:

```text
dsfgwegewg.mp3
```

with:

```text
ARTIST = Megadeth
TITLE  = Hangar 18
```

it will appear in-game as:

```text
Megadeth - Hangar 18
```

## Partial metadata

If only one tag exists, the patcher intelligently combines it with the filename when possible.

For example:

```text
Metadata TITLE = Hangar 18
Filename       = Megadeth - Hangar 18.mp3
```

becomes:

```text
Megadeth - Hangar 18
```

The same fallback also handles an inverted filename such as:

```text
Hangar 18 - Megadeth.mp3
```

when one of the metadata fields already tells the patcher which value is the artist or title.

The patcher also avoids duplicated display values. If Artist and Title resolve to the same text, it keeps the value only once instead of showing something like:

```text
Hangar 18 - Hangar 18
```

If `fetch_metadata` is `false`, only the filename parser is used.

---

# WARPED is always protected

```text
WARPED | event:/Music/Warped
```

WARPED is not discoverable in the Telefon in the stock game.

I made the choice that it cannot be replaced by the patcher and is never added to the Telefon setlist or unlocked playlists.

Even though it's the 9th song in the song bank, a file numbered `09` does **not** replace WARPED. It is simply a custom song.

The patcher also validates that WARPED was not modified before writing the final output.

---

# Playlist modes

The old Free Roam-only `stock / partial / full` system has been replaced.

The current option is:

```json
"playlist_mode": "full"
```

There are only 2 modes:

```text
stock
full
```

This setting applies to all 4 game MusicPlaylist objects handled by the patcher:

```text
Drift
FreeRoam
MainMenu
Racing
```

## `stock`

Keeps every original playlist unchanged.

No custom songs are injected into those automatic playlists.

`resources.assets` stays unchanged by the playlist patch.

This does **not** prevent custom music from being added to the Telefon.

## `full`

This is the default mode.

All 4 playlists are rebuilt to use:

```text
all 8 Telefon song slots
+
all custom songs
```

WARPED stays excluded.

This means Drift, FreeRoam, MainMenu and Racing can all use the complete final Telefon setlist.

---

# Important: replacement and playlists are separate

`playlist_mode` does **not** decide which stock songs are replaced.

Stock replacement is controlled only by the numbered files `01–08` present in `tracks`.

For example, if `05` is missing, Sirens remains stock whether:

```json
"playlist_mode": "stock"
```

or:

```json
"playlist_mode": "full"
```

If `05` exists, Sirens is replaced in both modes.

In short:

```text
tracks folder numbers
        ↓
controls stock replacement and custom order

playlist_mode
        ↓
controls the automatic game playlists
```

---

# Playlist command line override

You can override `playlist_mode` for one launch without editing `config.json`.

Stock:

```text
py -3 heatwarped_patcher.py --playlists stock
```

Full:

```text
py -3 heatwarped_patcher.py --playlists full
```

This only changes the current run.

---

# Volume normalization

The patcher supports 3 conversion normalization modes:

```text
lufs
peak
off
```

Set the mode with:

```json
"normalization_mode": "lufs"
```

---

## `lufs`

LUFS mode is intended to make different songs sound closer in perceived loudness.

It uses FFmpeg loudness analysis / normalization and can either **increase or decrease** a track's level to reach the target.

Relevant options:

```json
"target_lufs": -9.0,
"true_peak": -1.0
```

With the default config, tracks target approximately:

```text
-9 LUFS integrated
-1 dBTP maximum true peak
```

`true_peak` is a ceiling, not a value every song must hit exactly.

Accepted ranges in the current patcher:

```text
target_lufs: -70 to -5
true_peak:   -9 to 0
```

---

## `peak`

Peak mode is a dBFS normalization option.

Relevant option:

```json
"target_peak_dbfs": 0.0
```

If a track's peak is below the target, the patcher boosts it up toward the target.

If the track is already at or above the target, the patcher **does not reduce it**.

So peak mode is boost-only.

Accepted range:

```text
target_peak_dbfs: -60 to 0
```

---

## `off`

```json
"normalization_mode": "off"
```

Disables volume normalization completely.

The audio is still converted to the required game format, but no loudness/peak adjustment is applied.

---

# Configuration

Example `config.json`:

```json
{
  "_comment":
  [
    "stock = keep all playlists unchanged",
    "full = all 8 Telefon songs + all custom songs in every playlist",
    "normalization_mode = lufs, peak or off",
    "target_lufs and true_peak are used in lufs mode",
    "target_peak_dbfs is used in peak mode",
    "off = no volume normalization"
  ],

  "vorbis_quality": 10,
  "end_marker_policy": "full",
  "timeline_padding_ms": 0,
  "normalization_mode": "lufs",
  "target_lufs": -9.0,
  "true_peak": -1.0,
  "target_peak_dbfs": 0.0,
  "fetch_metadata": true,
  "playlist_mode": "full"
}
```

The comments are intentionally placed at the top so the actual options stay easy to read.

## `vorbis_quality`

Vorbis encoding quality used for converted tracks.

The patcher clamps this value to FFmpeg's supported range, up to `10`.

The provided config uses:

```json
"vorbis_quality": 10
```

## `end_marker_policy`

Controls how the FMOD end marker is adapted to replacement track length.

Possible values:

```text
full
preserve_original
preserve_tail
```

`full` is the normal/default choice for complete replacement songs.

The policies behave like this:

```text
full              -> move the End marker to the new track length
preserve_original -> keep the original absolute End marker
preserve_tail     -> keep the original gap between the trigger length and End marker
```

For normal music replacement, `full` is recommended.

## `timeline_padding_ms`

Adds optional extra time to the patched FMOD timeline.

Default:

```json
"timeline_padding_ms": 0
```

Normally this should stay at `0` unless you specifically need extra timeline padding.

## `fetch_metadata`

```json
"fetch_metadata": true
```

Enables Artist / Title tag reading and smart metadata fallback.

Set it to `false` if you want the patcher to use filenames only.

## `playlist_mode`

```json
"playlist_mode": "full"
```

Possible values:

```text
stock
full
```

`full` is the default.

---

# Custom track ordering summary

The final custom order is:

```text
1. Duplicate 01-08 files that were not used as the stock replacement
2. Numbered custom files 09-99, sorted numerically
3. Unnumbered / AUTO custom files, sorted alphabetically
```

When multiple files have the same number, they are ordered alphabetically by filename.

All custom tracks are compacted into consecutive internal slots automatically.

---

# Safety behavior

The patcher performs several checks before writing the output.

Among other things, it validates that:

- the original embedded FSB can be rebuilt safely
- stock music structures still match the supported game build
- custom FMOD graphs were created correctly
- the final Telefon references are valid
- playlist objects were patched without changing unrelated objects
- WARPED remained untouched

If a safety validation fails, the patcher stops instead of writing a knowingly broken output.

---

# Output / installation

After a successful patch, the patcher creates:

```text
output\Heatwarped_Data
output\track_manifest.json
output\patch_report.txt
```

`Heatwarped_Data` contains the patched game files. `track_manifest.json` and `patch_report.txt` give you a record of what the patcher generated.

Copy `output\Heatwarped_Data` to the Heatwarped game root and merge / replace the files when Windows asks.

That's it.
