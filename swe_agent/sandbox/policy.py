"""Política de comandos de primera línea (ADR-001, A3). Dueño: Antigravity.

Evalúa comandos mediante tokenización (shlex) con punctuation_chars=True en vez de coincidencia
ingenua de substrings o regex cruda.
Esto preserva comillas con operadores legítimos (`grep -E "a|b"`, `python -c "import sys; print(1)"`,
`echo 'a && b'`) sin bloquearlos. Si el parseo falla por sintaxis compleja, se permite (bash devolverá
el error sintáctico; la política es un freno defensivo, no la frontera de seguridad).
"""
from __future__ import annotations

import shlex
from typing import Sequence


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

        # Tokenizar preservando comillas y tratando operadores como puntuación
        try:
            lexer = shlex.shlex(cmd_str, posix=True, punctuation_chars=True)
            lexer.whitespace_split = False
            all_tokens = list(lexer)
        except Exception:
            # Si el parseo falla (p.ej. sintaxis compleja de bash con comillas no estándar),
            # permitimos que pase a bash (la política es solo un freno de primera línea)
            return None

        if not all_tokens:
            return None

        # Separadores de comandos y pipelines
        separators = {";", "&", "&&", "|", "||", "\n"}
        subcommands: list[list[str]] = []
        current: list[str] = []
        for t in all_tokens:
            if t in separators:
                if current:
                    subcommands.append(current)
                    current = []
            else:
                current.append(t)
        if current:
            subcommands.append(current)

        for tokens in subcommands:
            # Omitir asignaciones de variables de entorno al inicio (ej: VAR=1 pytest)
            i = 0
            while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
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
                for arg in args:
                    if not arg.startswith("-"):
                        if arg in self.banned_git_subcommands:
                            return f"comando no permitido por política: 'git {arg}' está bloqueado"
                        break

            # 3. Comandos destructivos (rm recursivo sobre ruta crítica)
            if exe == "rm":
                has_recursive = any(
                    arg in ("-r", "-R", "-rf", "-fr", "-rfi", "-rif")
                    or (arg.startswith("-") and ("r" in arg or "R" in arg))
                    for arg in args
                )
                if has_recursive:
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
