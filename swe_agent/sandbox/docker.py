"""DockerSandbox: contenedor efímero por episodio (ADR-001, ADR-009, A2). Dueño: Antigravity.

Reglas cumplidas:
  * Contenedor único por episodio con `sleep infinity`.
  * Montaje bind de cfg.workspace en /workspace (rw).
  * Aislamiento: --network none (salvo setup_cmd), --cap-drop=ALL, --security-opt no-new-privileges,
    límites de memoria, CPU y pids.
  * setup_cmd corre con red y luego se ejecuta `docker network disconnect`.
  * Envoltura con `timeout -s KILL <n>` dentro del contenedor para garantizar que matar el comando
    termine los procesos dentro del cgroup.
  * Fallas de infraestructura (daemon apagado, imagen ausente) levantan SandboxError.
  * close() destruye el contenedor de forma idempotente con `docker rm -f`.
"""
from __future__ import annotations

import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .base import CommandResult, Sandbox, SandboxError

if TYPE_CHECKING:
    from ..config import Config


def is_docker_available() -> bool:
    """Verifica si el cliente y daemon de Docker están disponibles y respondiendo."""
    try:
        p = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class DockerSandbox(Sandbox):
    """Sandbox aislado dentro de un contenedor Docker efímero."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.workspace = Path(cfg.workspace).resolve()
        if not self.workspace.is_dir():
            raise SandboxError(f"El workspace no existe o no es un directorio: {self.workspace}")

        if not is_docker_available():
            raise SandboxError(
                "Docker no está disponible o el daemon está detenido. "
                "Inicia Docker Desktop o usa sandbox='local'."
            )

        self.container_id = f"swe-sandbox-{uuid.uuid4().hex[:12]}"
        self._closed = False
        self._start_container()

    def _start_container(self) -> None:
        """Inicia el contenedor efímero configurado."""
        # Convertir ruta de Windows a formato aceptable por Docker CLI
        ws_str = str(self.workspace).replace("\\", "/")

        # Si hay setup_cmd, iniciamos con red (o bridge), ejecutamos setup_cmd y luego cortamos red
        has_setup = bool(self.cfg.setup_cmd and self.cfg.setup_cmd.strip())
        initial_net = "bridge" if has_setup else self.cfg.docker_network

        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            self.container_id,
            "--network",
            initial_net,
            "-v",
            f"{ws_str}:/workspace",
            "-w",
            "/workspace",
            "--memory",
            str(self.cfg.docker_memory),
            f"--cpus={self.cfg.docker_cpus}",
            f"--pids-limit={self.cfg.docker_pids}",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            self.cfg.docker_image,
            "sleep",
            "infinity",
        ]

        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=30.0, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SandboxError(f"Falla al iniciar el contenedor Docker: {e}") from e

        if p.returncode != 0:
            err = (p.stderr or p.stdout).strip()
            raise SandboxError(f"Docker run falló (exit={p.returncode}): {err}")

        # Ejecutar setup_cmd con red si fue provisto
        if has_setup:
            setup_res = self._exec_raw(self.cfg.setup_cmd, timeout=self.cfg.bash_timeout)
            # Desconectar la red si la configuración pide docker_network == 'none'
            if self.cfg.docker_network == "none":
                subprocess.run(
                    ["docker", "network", "disconnect", "bridge", self.container_id],
                    capture_output=True,
                    check=False,
                )
            if setup_res.exit_code != 0:
                self.close()
                raise SandboxError(
                    f"cfg.setup_cmd falló (exit={setup_res.exit_code}): {setup_res.output}"
                )

    def _exec_raw(self, cmd: str, timeout: float) -> CommandResult:
        """Ejecuta un comando crudo dentro del contenedor."""
        return self.run(cmd, timeout=timeout)

    def run(self, cmd: str, timeout: float) -> CommandResult:
        """Ejecuta un comando dentro del contenedor envuelto en timeout -s KILL."""
        if self._closed:
            raise SandboxError("El sandbox ya ha sido cerrado")

        start_t = time.perf_counter()

        # Envolver dentro del contenedor con 'timeout -s KILL <segundos>'
        # Esto asegura que el kernel dentro del contenedor mate todo el árbol de procesos
        timeout_int = max(1, int(timeout))
        quoted_cmd = shlex.quote(cmd)
        wrapped_bash = f"timeout -s KILL {timeout_int} bash -c {quoted_cmd}"

        docker_exec_cmd = [
            "docker",
            "exec",
            "-i",
            "-w",
            "/workspace",
            "-e",
            "GIT_PAGER=cat",
            "-e",
            "PAGER=cat",
            "-e",
            "TERM=dumb",
            "-e",
            "NO_COLOR=1",
            "-e",
            "PYTHONIOENCODING=utf-8",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            self.container_id,
            "bash",
            "-c",
            wrapped_bash,
        ]

        # Damos un margen en el host para que el timeout interno de coreutils actúe primero
        host_timeout = timeout + 15.0

        try:
            p = subprocess.Popen(
                docker_exec_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            raw_out, _ = p.communicate(timeout=host_timeout)
            duration_s = time.perf_counter() - start_t
            out_str = (raw_out or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
            exit_code = p.returncode

            # Si timeout dentro de linux expiró, devuelve 124 o 137 (SIGKILL)
            timed_out = exit_code in (124, 137)
            if timed_out:
                exit_code = 124

            return CommandResult(
                exit_code=exit_code,
                output=out_str,
                timed_out=timed_out,
                duration_s=duration_s,
            )

        except subprocess.TimeoutExpired:
            # Si el host timeout expiró (caso extremo donde docker exec se trabó)
            p.kill()
            try:
                raw_out, _ = p.communicate(timeout=2.0)
            except Exception:
                raw_out = b""
            duration_s = time.perf_counter() - start_t
            out_str = (raw_out or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
            return CommandResult(
                exit_code=124,
                output=out_str,
                timed_out=True,
                duration_s=duration_s,
            )
        except OSError as e:
            raise SandboxError(f"Error al ejecutar docker exec: {e}") from e

    def close(self) -> None:
        """Destruye el contenedor efímero de manera idempotente."""
        if self._closed:
            return
        self._closed = True
        try:
            subprocess.run(
                ["docker", "rm", "-f", self.container_id],
                capture_output=True,
                timeout=15.0,
                check=False,
            )
        except Exception:
            pass
