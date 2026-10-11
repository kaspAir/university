"""Secret references resolve only inside the gateway process (chapter 8: keys never in prompts or exports).

`secret://providers/anthropic` -> environment variable CAMPUS_SECRET_PROVIDERS_ANTHROPIC.
"""
from __future__ import annotations

import os
import re

from .port import SecretUnavailable

_PREFIX = "secret://"


def env_name_for(secret_ref: str) -> str:
    if not secret_ref.startswith(_PREFIX):
        raise SecretUnavailable(f"secret_ref muss mit {_PREFIX} beginnen")
    path = secret_ref[len(_PREFIX):].strip("/")
    if not path:
        raise SecretUnavailable("secret_ref ohne Pfad")
    return "CAMPUS_SECRET_" + re.sub(r"[^A-Za-z0-9]+", "_", path).upper()


class EnvSecretResolver:
    def resolve(self, secret_ref: str | None) -> str:
        if not secret_ref:
            raise SecretUnavailable("Deployment hat keine secret_ref")
        name = env_name_for(secret_ref)
        value = os.environ.get(name)
        if not value:
            raise SecretUnavailable(f"Umgebungsvariable {name} ist nicht gesetzt")
        return value
