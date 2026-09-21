# Third-party software

eki is MIT licensed. It depends on, and (when packaged) redistributes, the
following, each under its own licence:

| Component | Licence | Used for |
| --- | --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | the local HTTP API |
| [Starlette](https://github.com/encode/starlette) | BSD-3-Clause | ASGI plumbing under FastAPI |
| [uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | the server that runs it |
| [pydantic](https://github.com/pydantic/pydantic) | MIT | request bodies |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | talking to model servers |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT | reading config.yaml |
| [keyring](https://github.com/jaraco/keyring) | MIT | API keys in the macOS Keychain |
| [CPython](https://github.com/python/cpython) (via [python-build-standalone](https://github.com/astral-sh/python-build-standalone)) | PSF-2.0 | the interpreter bundled inside Eki.app |

eki *drives* these, and does not redistribute them:

- **Claude Code** and **Codex** — run as the user's own installed CLIs, with the
  user's own subscription. eki never reads, copies or reuses their credentials,
  and stores no login of its own for them. Their terms are between the user and
  their provider.
- **mlx_lm**, **MLX** (MIT) and **ComfyUI** (GPL-3.0) — run as separate local
  servers the user installs. eki starts and stops them and speaks HTTP to them.
- Model weights — downloaded from Hugging Face under each model's own licence,
  which eki shows before downloading.

Names like Claude, Codex, OpenAI, Anthropic and Hugging Face are trademarks of
their owners. eki is not affiliated with, endorsed by, or built by any of them.
