"""Configuración de un episodio. Contrato compartido: agregar campos, nunca renombrar."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    workspace: Path                       # repo objetivo en el HOST (ADR-001)

    # --------------------------------------------------------------- loop
    max_steps: int = 40

    # --------------------------------------------------------------- ACI
    view_page: int = 100                  # líneas por página de view
    max_obs_chars: int = 6_000            # 10k era demasiado para 8B con 16k de contexto
    bash_timeout: float = 120.0
    lint_on_edit: bool = True             # rechaza ediciones que rompen la sintaxis

    # --------------------------------------------------------------- git (ADR-005)
    agent_branch_prefix: str = "agent/"
    commit_each_edit: bool = True

    # --------------------------------------------------------------- stuck (ADR-008)
    stuck_repeats: int = 3                # misma firma N veces => abortar
    stuck_warn_first: bool = True         # avisar una vez al modelo antes de abortar

    # --------------------------------------------------------------- contexto (ADR-007)
    ctx_budget_tokens: int = 12_000       # prompt máximo (num_ctx 16k menos margen de respuesta)
    condense_block: int = 6               # K: se condensa de a bloques para no mover el prefijo

    # --------------------------------------------------------------- tarea (ADR-004)
    test_cmd: str | None = None           # pista opcional: cómo correr los tests del repo
    setup_cmd: str | None = None          # instalación de deps; única fase con red en Docker

    # --------------------------------------------------------------- sandbox (ADR-001, ADR-009)
    sandbox: str = "docker"               # "docker" | "local"
    docker_image: str = "swe-agent-sandbox:latest"
    docker_memory: str = "4g"
    docker_cpus: float = 4.0
    docker_pids: int = 256
    docker_network: str = "none"

    # --------------------------------------------------------------- logs (ADR-006)
    log_dir: Path = field(default_factory=lambda: Path("logs"))

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.log_dir = Path(self.log_dir).resolve()
        # El log dentro del repo ensucia git status y termina commiteado en el parche
        if self.log_dir.is_relative_to(self.workspace):
            raise ValueError(f"log_dir ({self.log_dir}) no puede estar dentro del workspace")
        if self.sandbox not in ("docker", "local"):
            raise ValueError(f"sandbox desconocido: {self.sandbox}")
