# Third-party software

eki is licensed under the Apache License 2.0. It depends on, and (when packaged) redistributes, the
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
| [pyte](https://github.com/selectel/pyte) | LGPL-3.0 | rendering Claude Code's /usage panel to read it, used unmodified |
| [CPython](https://github.com/python/cpython) (via [python-build-standalone](https://github.com/astral-sh/python-build-standalone)) | PSF-2.0 | the interpreter bundled inside Eki.app |

eki ships a snapshot of public benchmark results, reduced to one number per
model per kind of work (`eki/evals/public_scores.json`):

| Data | Licence | Used for |
| --- | --- | --- |
| [Epoch AI, *Capabilities & Benchmarking*](https://epoch.ai/benchmarks) | CC BY 4.0 | starting beliefs about what each model is good at, before eki has measured anything itself |
| [OpenEvals/leaderboard-data](https://huggingface.co/datasets/OpenEvals/leaderboard-data) (Hugging Face) | MIT | the same, for open-weight models |
| [OpenRouter model list](https://openrouter.ai/models) | public API | API prices, which become relative cost weights |

eki measures models itself with items sampled from public benchmark datasets,
fetched on first use from the Hugging Face datasets server into `~/.eki/bench/`
(nothing is redistributed with eki):

| Dataset | Licence |
| --- | --- |
| [GSM8K](https://huggingface.co/datasets/openai/gsm8k) (Cobbe et al. 2021) | MIT |
| [MATH](https://huggingface.co/datasets/EleutherAI/hendrycks_math) (Hendrycks et al. 2021) | MIT |
| [AIME 2025](https://huggingface.co/datasets/math-ai/aime25) | Apache-2.0 |
| [MBPP](https://huggingface.co/datasets/google-research-datasets/mbpp) (Austin et al. 2021) | CC BY 4.0 |
| [HumanEval](https://huggingface.co/datasets/openai/openai_humaneval) (Chen et al. 2021) | MIT |
| [TriviaQA](https://huggingface.co/datasets/mandarjoshi/trivia_qa) (Joshi et al. 2017) | Apache-2.0 |
| [MMLU-Pro](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro) (Wang et al. 2024) | MIT |

Benchmark questions and answers remain the property of their creators; eki
carries only the published scores. Rebuild with
`python -m eki.evals.build_public_scores`.

**Mermaid** (MIT, © Knut Sveidqvist and contributors) — `mermaid.min.js` is
fetched at build time and carried in the app's Resources so diagram artifacts
draw offline. It is not in this repository.

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
