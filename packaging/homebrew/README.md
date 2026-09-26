# Homebrew install for clipstashd

The in-repo formula `clipstash.rb` installs the `clipstashd` entry point
(`pip install`s this repo's helper package plus its PyYAML + Pillow runtime
deps into a Homebrew-managed virtualenv). `brew install` needs network access
to fetch those deps from PyPI.

Install the latest `main` (HEAD):

```bash
brew install --HEAD --formula https://raw.githubusercontent.com/eriksjaastad/clipstash/main/packaging/homebrew/clipstash.rb
```

Or from a local checkout:

```bash
brew install --formula ./packaging/homebrew/clipstash.rb
```

A `stable` block (GitHub archive, `sha256 :no_check`, version `0.1.0`) is
included for non-HEAD installs; this is a personal tap, so the formula is not
signed or notarized.

After install, `clipstashd` is on your PATH. For a login LaunchAgent, follow
the `caveats` printed by brew or the comments in
`packaging/macos/com.clipstash.helper.plist`.
