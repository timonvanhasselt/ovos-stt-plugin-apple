# ovos-stt-plugin-apple

An [OVOS](https://openvoiceos.org) speech-to-text plugin for **macOS** that uses
Apple's Speech framework (`SFSpeechRecognizer`) through a small native Swift
helper. The helper runs as one persistent process and talks to the plugin via
JSON lines over stdin/stdout, so there is no per-utterance startup cost.

Features:

- On-device recognition when a local model is available, with optional
  fallback to Apple's servers
- Persistent helper process with automatic restart on crash or timeout
- Helper is compiled automatically (or with `ovos-stt-apple-build`)
- Trailing-silence padding to avoid a cut-off last word
- One-shot mode as a fallback (`persistent: false`)

## Requirements

- macOS 10.15 or newer (developed and tested on macOS Sequoia 15.2)
- Xcode command line tools (`xcode-select --install`) are only needed if the
  prebuilt helper cannot be downloaded and has to be compiled locally
- Python 3.9+ and an OVOS installation (`ovos-plugin-manager`)

> macOS only. The plugin raises an error on Linux and Windows.

## Installation

```bash
# inside your OVOS virtualenv
pip install git+https://github.com/timonvanhasselt/ovos-stt-plugin-apple.git
# or from a local clone: pip install .
ovos-stt-apple-build        # optional: the helper is also installed on first use
```

The helper is installed by downloading the prebuilt binary for your Mac
(`arm64` for Apple Silicon, `x86_64` for Intel) from the GitHub release that
matches the plugin version. The SHA256 checksum is verified. If the download
fails, the plugin compiles the bundled Swift source instead (needs Xcode
command line tools). It is installed to:

```
~/Library/Application Support/ovos_stt_apple/apple-stt-helper
```

Options of `ovos-stt-apple-build`:

| Flag | Meaning |
|---|---|
| `--download` | only download the release binary |
| `--compile` | only compile from source (useful after a macOS update, or to audit what you run) |
| `--force` | replace an existing helper |
| `--dest PATH` | install somewhere else (then set `helper_path` in the config) |

## Configuration

Edit `~/.config/mycroft/mycroft.conf`.

### Minimal

```json
{
  "stt": {
    "module": "ovos-stt-plugin-apple",
    "ovos-stt-plugin-apple": {
      "lang": "en-US"
    }
  }
}
```

### Full example (all options with their defaults)

```json
{
  "lang": "en-US",
  "stt": {
    "module": "ovos-stt-plugin-apple",
    "ovos-stt-plugin-apple": {
      "lang": "en-US",
      "helper_path": "~/Library/Application Support/ovos_stt_apple/apple-stt-helper",
      "auto_build": true,
      "allow_server": true,
      "prefer_on_device": true,
      "persistent": true,
      "pad_ms": 500,
      "timeout": 30
    }
  }
}
```

### Strictly local (no Apple servers)

```json
{
  "stt": {
    "module": "ovos-stt-plugin-apple",
    "ovos-stt-plugin-apple": {
      "lang": "en-US",
      "allow_server": false
    }
  }
}
```

This requires an on-device model for the chosen language. If none is
available, recognition returns an empty string and an error is logged.

### Options

| Key | Default | Description |
|---|---|---|
| `lang` | OVOS default | Locale such as `en-US` or `nl-NL`. Short codes (`nl`, `en`) are expanded automatically. |
| `helper_path` | see above | Path to the compiled `apple-stt-helper` binary. |
| `auto_build` | `true` | Compile the helper if it is missing. |
| `allow_server` | `true` | Allow Apple's servers when no on-device model exists. |
| `prefer_on_device` | `true` | Try on-device first, then fall back to the server (only if `allow_server` is true). |
| `persistent` | `true` | Keep one helper process running between requests. |
| `pad_ms` | `500` | Milliseconds of silence appended to each utterance, which prevents the last word from being cut off. |
| `timeout` | `30` | Per-request timeout in seconds. |

## Permissions

Speech Recognition permission (TCC) belongs to the app that launches the
helper, not to the helper itself.

1. Run OVOS once from **Terminal.app** and accept the permission prompt.
2. Or add your terminal app under *System Settings > Privacy & Security >
   Speech Recognition*.
3. A launchd service may need its own grant, so test from a terminal first.

## Known limitation: missing first word with `hybrid_listen`

If the first word is missing with `hybrid_listen: true`, check the audio saved
by the listener (`"save_utterances": true` in the `listener` section, files go to
`~/.local/share/mycroft/listener/utterances`). If the word is already absent
there, it is lost before the audio reaches this plugin; the plugin cannot
restore it. In that case look at the listener's `speech_begin` and VAD settings,
or use the wake word instead of hybrid listen.

## Privacy and limits

- With `allow_server: true`, audio may be sent to Apple when no on-device
  model is present. Set `allow_server: false` to keep everything local.
- Apple limits a request to roughly one minute of audio and may throttle
  heavy server usage.
- Server mode requires an internet connection.
- Whether an on-device model exists depends on the macOS version and
  language. Check with `--list` (see below).

## Troubleshooting and manual testing

```bash
H=~/"Library/Application Support/ovos_stt_apple/apple-stt-helper"

$H --auth                                    # prints authorization status (3 = authorized)
$H --list 2>/dev/null | grep -E "en-US|nl-NL"  # on-device support per locale (true/false)

say -v Samantha "Hello, this is a test" -o test.aiff
afconvert -f WAVE -d LEI16@16000 -c 1 test.aiff test.wav
$H --server --lang en-US --file test.wav     # one-shot recognition via Apple servers
```

Test the persistent protocol directly:

```bash
printf '%s\n' '{"id":"1","cmd":"ping"}' \
  '{"id":"2","cmd":"recognize","file":"'$PWD'/test.wav","lang":"en-US","server":true}' \
  | (cat; sleep 5) | "$H" --serve
```

Common messages:

| Message | Meaning |
|---|---|
| `No Assistant asset for language ...` (stderr) | No on-device model for that locale. Use `allow_server: true`. |
| `no on-device model for ...` (exit code 4) | Same, when `allow_server` is false. |
| `speech permission denied` (exit code 5) | Grant access under Privacy & Security > Speech Recognition. |
| `timeout` (exit code 6) | Recognition took too long. Check the network in server mode. |
| Last word missing | Increase `pad_ms`. |

## Helper protocol

`apple-stt-helper --serve` reads and writes one JSON object per line.

```
<- {"ready":true,"auth":3}
-> {"id":"1","cmd":"recognize","file":"/tmp/a.wav","lang":"en-US","server":true,"timeout":30}
<- {"id":"1","text":"hello world","code":0}
-> {"id":"2","cmd":"languages"}
<- {"id":"2","languages":[{"id":"en-US","on_device":false}, ...]}
-> {"id":"3","cmd":"ping"}
<- {"id":"3","pong":true}
```

Errors are returned as `{"id":"...","error":"...","code":N}`.

## Releasing (maintainers)

1. Bump `ovos_stt_apple/version.py`.
2. Release, either way:
   - command line: `git tag v0.1.1 && git push origin v0.1.1`
   - GitHub website: *Releases > Draft a new release*, create the tag `v0.1.1` on publish
   - GitHub website: *Actions > Release helper binaries > Run workflow*, fill in
     the tag `v0.1.1` (leave the field empty to only build and keep the binaries as artifacts)

The workflow in `.github/workflows/release.yml` builds `apple-stt-helper-arm64`
and `apple-stt-helper-x86_64` on a macOS runner, ad-hoc signs them, writes
`SHA256SUMS`, and attaches all three files to the GitHub release. The tag must
match the package version, because the plugin downloads the release that
matches its own version.

The binaries are ad-hoc signed, not notarized. Files fetched by the plugin
(via Python) normally carry no quarantine flag. If you download a binary
manually in a browser, macOS may block it; run
`xattr -d com.apple.quarantine apple-stt-helper-*` or use `ovos-stt-apple-build`.

## Project layout

```
ovos_stt_apple/
  __init__.py      AppleSTT plugin and config
  helper.py        persistent helper client
  build.py         downloads or compiles the Swift helper
  version.py       package version (also selects the release to download)
  swift/main.swift native helper
  swift/Info.plist embedded plist (speech usage description)
tests/
.github/workflows/release.yml   builds and publishes helper binaries
```

## License

Apache-2.0
