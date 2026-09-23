# Homebrew

`brew install REDES01/eki/eki` reads `Formula/eki.rb` from the tap repo,
[REDES01/homebrew-eki](https://github.com/REDES01/homebrew-eki). The formula
is generated here, for each release:

```sh
git tag v0.3.0 && git push origin v0.3.0
.venv/bin/python packaging/homebrew/make_formula.py 0.3.0 > ../homebrew-eki/Formula/eki.rb
# then commit and push the tap
```

It needs `uv` (to resolve `requirements.txt` for macOS and Linux, Intel and
Arm) and reads each wheel's SHA-256 from PyPI, so the install downloads
exactly what was pinned and builds nothing. To try a formula before tagging,
pass `--url file:///path/to/eki-X.Y.Z.tar.gz` and install it from a local
tap (`brew tap-new`).

What the formula sets up: eki's source and seed config in `libexec`, a
virtualenv beside them, and an `eki` command that sets `EKI_INSTALL=homebrew`
— which is how eki knows not to change its own code and to hand the engine
to `brew services`.
