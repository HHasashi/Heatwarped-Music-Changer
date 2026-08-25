# Heatwarped Music Patcher

A custom music patcher for **Heatwarped**.

The patcher lets you replace the original Jukebox songs, add completely new songs to the game, and choose which songs can be used by the Free Roam music playlist.

You can replace only the songs you want while leaving every other stock song untouched.

You can also add custom songs without replacing any of the original 8 songs.

> **WARPED, the intro song, is always protected and will never be replaced.**

---

## Features

- Replace any of the 8 stock Jukebox songs individually
- Keep any stock song you don't want to replace
- Add additional custom songs from slot `09` onward
- Skip numbers without creating empty tracks
- Automatically display the custom Artist / Title in-game
- Choose the Free Roam playlist size
- Automatically convert supported audio files
- Automatically download the required tools if enabled
- WARPED is always protected

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

You don't need to convert the songs yourself, the patcher automatically converts them to the format required by the game.

[Python 3](https://www.python.org/) is required before launching the script if you plan on compiling this yourself. If not, I'd recommend downloading the compiled EXE files in the RELEASE section.

[FFMPEG](https://www.gyan.dev/ffmpeg/builds/) and [oggvorbis2fsb5](https://github.com/uyjulian/oggvorbis2fsb5/releases/latest/) are required but will be downloaded at the first launch.

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

Tracks must follow this filename format:

```text
NN - Artist - Title.extension
```

Example:

```text
01 - Avenged Sevenfold - Blinded in Chains.mp3
```

The spaces and hyphens are important.

Files that don't follow this format will not be recognized.

---

## 3. Run the patcher

I'd recommend downloading the compiled EXE files in the RELEASE section, but in case you don't or the EXE does not work because I'm careless...

You can run the patched via the ```RUN_PATCHER.bat``` or via this command line :

```text
py -3 heatwarped_patcher.py
```

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

# Detailed usage

## 1. Replacing the 8 stock songs — 01 to 08

Numbers `01` to `08` correspond directly to the 8 original Jukebox songs.

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

A stock song is replaced **only if its number exists in the `tracks` folder**.

For example:

```text
01
02
03
04
06
07
08
```

but no `05` means:

```text
Sirens remains unchanged.
```

You don't need to provide all 8 songs.

If you only want to replace Carbon and Black, you can simply use:

```text
01 - Artist - Song.mp3
06 - Artist - Song.mp3
```

The other 6 stock songs will stay untouched.

If your custom setlist starts directly at `09`, then **none of the 8 stock songs will be replaced**.

---

## 2. WARPED is always protected

```text
WARPED | event:/Music/Warped
```

WARPED is the intro song.

I made the choice to keep it permanently protected.

It cannot be replaced by the patcher.

A file numbered `09` does **NOT** replace WARPED.

Instead, `09` becomes the first additional custom song.

---

## 3. Adding custom music — 09 to 99

Starting from `09`, numbers no longer correspond to existing game songs.

They are only used to define the **order of your custom songs**.

Example:

```text
09 - Artist - Song A.mp3
10 - Artist - Song B.mp3
11 - Artist - Song C.mp3
```

will add 3 custom songs in that order.

You can also skip numbers.

Example:

```text
09 - Artist - Song A.mp3
10 - Artist - Song B.mp3
12 - Artist - Song C.mp3
```

The patcher will still create 3 consecutive custom songs.

There will NOT be an empty track where `11` should have been.

Another example:

```text
10 - Artist - Song A.mp3
12 - Artist - Song B.mp3
```

will simply create 2 custom songs.

The patcher takes every existing file above `08`, sorts them numerically, and automatically compacts the custom setlist.

This means you can reorganize or remove songs without having to renumber everything after them.

---

# Free Roam playlist

By default, Heatwarped only uses tracks:

```text
2
3
4
```

for the automatic Free Roam playlist.

Other songs can still be played through the Telefon, but they are not normally part of the Free Roam rotation.

The patcher lets you change this behavior.

In `config.json`:

```json
"free_roam_playlist": "partial"
```

There are 3 possible modes.

---

## `stock`

Keeps the original Free Roam playlist length.

```text
2
3
4
```

Only the original Free Roam playlist is used.

Custom songs are not added to the automatic Free Roam rotation.

---

## `partial`

This is the default mode.

Free Roam contains:

```text
2
3
4
+
all custom songs added from 09 onward
```

Tracks numbered:

```text
1
5
6
7
8
```

are not added to the Free Roam playlist.

WARPED also stays excluded.

---

## `full`

Free Roam contains:

```text
all 8 Jukebox song slots
+
all custom songs
```

WARPED still stays excluded.

---

# Important: replacement and Free Roam are separate

The `stock`, `partial` and `full` options **ONLY control the Free Roam playlist**.

They do NOT decide which original songs are replaced.

Stock song replacement is controlled **only by the numbered files present in the `tracks` folder**.

For example:

If `05` is NOT present:

```text
05 - Artist - Song.mp3
```

then Sirens will remain unchanged, regardless of whether Free Roam is set to:

```text
stock
partial
full
```

If `05` IS present, Sirens will be replaced regardless of the Free Roam
