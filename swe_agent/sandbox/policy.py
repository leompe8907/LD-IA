"""Política de comandos de primera línea (ADR-001, A3). Dueño: Antigravity.

Evalúa comandos mediante tokenización (shlex) en vez de coincidencia ingenua de substrings.
Esto permite distinguir un comando real como `curl https://...` de un comando legítimo
como `echo "curl es genial"` o `pytest -k test_curl`.
"""
from __future__ import annotations

import re
import shlex
from typing import Sequence

# Separadores de comandos en shell
_CMD_SPLIT_RE = re.compile(r"(?:&&|\|\||[;&|\n])")


class DefaultPolicy:
    """Política defensiva basada en tokens.

    Freno de primera línea para evitar errores comunes o comandos claramente destructivos
    o con intento de exfiltración de red. La seguridad real y aislamiento la provee el sandbox.
    """

    def __init__(
        self,
        banned_executables: Sequence[str] = (
            "sudo",
            "su",
            "curl",
            "wget",
            "nc",
            "netcat",
            "ncat",
            "shutdown",
            "reboot",
            "poweroff",
            "mkfs",
        ),
        banned_git_subcommands: Sequence[str] = ("push",),
    ):
        self.banned_executables = set(banned_executables)
        self.banned_git_subcommands = set(banned_git_subcommands)

    def check(self, cmd: str) -> str | None:
        """Verifica el comando.

        Retorna None si está permitido, o un mensaje explicativo si está prohibido.
        """
        cmd_str = cmd.strip()
        if not cmd_str:
            return None

        # Detección temprana de fork bombs típicas
        compact = "".join(cmd_str.split())
        if ":(){:|:&};:" in compact:
            return "comando no permitido por política: fork bomb detectada"

        # Dividir pipelines y comandos encadenados (;, &&, ||, |, newline)
        subcmds = _CMD_SPLIT_RE.split(cmd_str)

        for sub in subcmds:
            sub = sub.strip()
            if not sub:
                continue

            try:
                tokens = shlex.split(sub, posix=True)
            except ValueError as e:
                return f"comando con sintaxis inválida: {e}"

            if not tokens:
                continue

            # Omitir asignaciones de variables de entorno al inicio (ej: VAR=1 pytest)
            i = 0
            while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
                # Si es asignación del tipo FOO=bar
                var_name = tokens[i].split("=", 1)[0]
                if var_name.isidentifier():
                    i += 1
                else:
                    break

            if i >= len(tokens):
                continue

            exe = tokens[i].replace("\\", "/").rstrip("/").split("/")[-1]
            args = tokens[i + 1 :]

            # 1. Comandos prohibidos directos
            if exe in self.banned_executables:
                return f"comando no permitido por política: '{exe}' está bloqueado"

            # 2. Subcomandos de git (ej. git push)
            if exe == "git":
                # Buscar el primer subcomando no-opción
                for arg in args:
                    if not arg.startswith("-"):
                        if arg in self.banned_git_subcommands:
                            return f"comando no permitido por política: 'git {arg}' está bloqueado"
                        break

            # 3. Comandos destructivos (rm recursivo sobre raíz o paths críticos)
            if exe == "rm":
                has_recursive = any(
                    arg in ("-r", "-R", "-rf", "-fr", "-rfi", "-rif")
                    or (arg.startswith("-") and ("r" in arg or "R" in arg))
                    for arg in args
                )
                if has_recursive:
                    # Chequear targets
                    for arg in args:
                        if arg.startswith("-"):
                            continue
                        clean_arg = arg.strip().rstrip("/")
                        if clean_arg in (
                            "",
                            "/",
                            "/*",
                            "~",
                            "~/*",
                            ".",
                            "..",
                            "/etc",
                            "/usr",
                            "/var",
                            "/bin",
                            "/sbin",
                            "/root",
                            "C:",
                            "C:\\",
                            "C:/*",
                        ) or clean_arg.startswith(("/etc", "/usr", "/bin", "/sbin")):
                            return (
                                f"comando no permitido por política: rm recursivo sobre ruta crítica '{arg}'"
                            )

        return None
