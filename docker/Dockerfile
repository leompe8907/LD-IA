# Imagen base del sandbox para Mini-SWE-Agent (ADR-004, ADR-009, A5)
# Base ligera pero completa para ejecución de tests y comandos sin red
FROM python:3.12-slim

# Evitar prompts interactivos
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    GIT_PAGER=cat \
    PAGER=cat \
    TERM=dumb \
    NO_COLOR=1

# Instalar dependencias mínimas de sistema (git, bash, coreutils para timeout)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    git \
    coreutils \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instalar pytest en el entorno de Python
RUN pip install --no-cache-dir pytest

# Directorio de trabajo del sandbox
WORKDIR /workspace

# Comando por defecto para mantener el contenedor vivo en background
CMD ["sleep", "infinity"]
