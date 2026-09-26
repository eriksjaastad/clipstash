class Clipstash < Formula
  include Language::Python::Virtualenv

  desc "Local helper for clipstash video-still + title + URL packets"
  homepage "https://github.com/eriksjaastad/clipstash"
  url "https://github.com/eriksjaastad/clipstash/archive/refs/heads/main.tar.gz"
  version "0.1.0"
  sha256 :no_check
  license "MIT"
  head "https://github.com/eriksjaastad/clipstash.git", branch: "main"

  depends_on "python@3.12"

  def install
    venv = virtualenv_create(libexec, "python3.12")
    # Personal tap, not homebrew-core: install the helper package with the
    # venv's own pip so PyYAML + Pillow resolve from PyPI at install time.
    # (venv.pip_install would pass --no-deps, so the runtime deps would be
    # skipped unless pinned as resources.) brew install needs network for this.
    system libexec/"bin/python", "-m", "ensurepip", "--upgrade"
    system libexec/"bin/python", "-m", "pip", "install",
           "--no-compile", "--no-warn-script-location", buildpath
    bin.install_symlink libexec/"bin/clipstashd"
    (pkgshare).install "packaging/macos/com.clipstash.helper.plist"
  end

  def caveats
    <<~EOS
      LaunchAgent template installed to:
        #{opt_pkgshare}/com.clipstash.helper.plist

      Install as a per-user LaunchAgent:
        cp #{opt_pkgshare}/com.clipstash.helper.plist ~/Library/LaunchAgents/
        # edit @@CLIPSTASHD@@ so ProgramArguments points at #{opt_bin}/clipstashd
        launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.clipstash.helper.plist

      Unload:
        launchctl bootout gui/$(id -u)/com.clipstash.helper

      Or build the single binary and run scripts/install_macos_helper.sh from the
      repo checkout; it substitutes @@CLIPSTASHD@@ automatically.
    EOS
  end

  test do
    assert_match "clipstash", shell_output("#{bin}/clipstashd --help")
  end
end
