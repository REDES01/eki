"""Backend registry. Importing a module here registers its kind."""
from .base import (  # noqa: F401
    Backend, BackendError, BackendInfo, Capabilities, Cost, Health, Message,
    build, kinds, register,
)

from . import claude_code  # noqa: F401,E402
from . import codex  # noqa: F401,E402
from . import comfyui  # noqa: F401,E402
from . import mlx  # noqa: F401,E402
from . import anthropic_api  # noqa: F401,E402
from . import openai_compat  # noqa: F401,E402
