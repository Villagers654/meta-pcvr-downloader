# Meta PCVR Downloader

Download and verify Meta/Oculus **PCVR (Rift)** games you own, directly on Linux.

The downloader finds the available PCVR builds for a Meta store app, downloads every
unique content segment once, verifies its SHA-256 hash, and safely assembles the files
described by Meta's signed-in build manifest. Interrupted runs can reuse the cache.

> [!IMPORTANT]
> This is an unofficial interoperability tool. It does not bypass purchases or
> entitlement checks. You must be signed into an account that owns the PCVR/Rift build.
> Meta can change its private APIs at any time.

## Fastest setup

Requirements: Linux, Python 3.10+, and Firefox, Edge, Chrome, or Chromium.

1. Sign in to [meta.com](https://www.meta.com/) in your browser.
2. Chromium-family browsers only: install `secret-tool` if it is not already present.
   Firefox does not need it.

   - Fedora/Bazzite: `sudo dnf install libsecret`
   - Ubuntu/Debian: `sudo apt install libsecret-tools`
   - Arch: `sudo pacman -S libsecret`

3. Install the downloader:

   ```bash
   git clone https://github.com/Villagers654/meta-pcvr-downloader.git
   cd meta-pcvr-downloader
   ./install.sh
   ```

4. Paste a Meta PCVR store URL into one command:

   ```bash
   meta-pcvr download 'https://www.meta.com/experiences/pcvr/vader-immortal-episode-i/2031736060288351/'
   ```

The default destination is `~/Games/Meta PCVR/<game name>`.

## Common commands

List every available PCVR build:

```bash
meta-pcvr versions 2031736060288351
```

Download a particular version and choose the destination:

```bash
meta-pcvr download 2031736060288351 \
  --build '1.1.0+236956.Shipping' \
  --output "$HOME/Games/Vader Immortal Episode I"
```

Preview what would be selected without downloading:

```bash
meta-pcvr download 2031736060288351 --dry-run
```

Run `meta-pcvr download --help` for cache, concurrency, and non-interactive options.

## Authentication

By default the tool searches native, Flatpak, and Snap profiles for Firefox, plus normal
and Flatpak profiles for Microsoft Edge, Google Chrome, and Chromium. It reads the
`oc_ac_at` Meta login cookie locally. Firefox stores this cookie directly in its profile;
Chromium-family browsers are decrypted through your desktop Secret Service. The token is
kept in memory and is never printed, logged, or saved by this tool.

For unsupported browsers, provide your own short-lived token through the environment:

```bash
read -rsp 'Meta access token: ' META_OCULUS_ACCESS_TOKEN; echo
export META_OCULUS_ACCESS_TOKEN
meta-pcvr versions 2031736060288351
unset META_OCULUS_ACCESS_TOKEN
```

Avoid putting access tokens directly in command-line arguments or issue reports.

## Why this downloader exists

Some existing Rift downloaders race when multiple manifest files reference the same
content segment: one worker moves the shared temporary file while another still needs
it. This downloader uses a content-addressed cache, downloads each unique hash once,
and assembles files only after all segments have passed verification.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

## Credits and license

The Meta GraphQL and CDN behavior was studied with reference to
[ComputerElite/Oculus-downgrader](https://github.com/ComputerElite/Oculus-downgrader)
and [ptrpaws/DownQuest](https://github.com/ptrpaws/DownQuest). No code or credentials
from either project are bundled.

Released under the [MIT License](LICENSE).
