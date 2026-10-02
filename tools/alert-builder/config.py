"""Load Watchtower settings from the environment or the team config file."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

# Tests override this with a temp directory. Do not inline the path.
CONFIG_DIR = "/config"

_TRUE = {"1", "true"}


@dataclass
class Settings:
    vss_url: str
    vss_username: str
    vss_password: str = field(repr=False)
    wandb_api_key: str = field(repr=False)
    wandb_project_path: str
    llm_base_url: str = "https://api.inference.wandb.ai/v1"
    llm_model: str = ""
    webhook_url: str = ""
    port: int = 8080
    data_dir: str = "/tmp/alert-builder"
    mock: bool = False

    def __repr__(self) -> str:
        return (
            "Settings("
            f"vss_url={self.vss_url!r}, "
            f"vss_username={self.vss_username!r}, "
            "vss_password='***', "
            "wandb_api_key='***', "
            f"wandb_project_path={self.wandb_project_path!r}, "
            f"llm_base_url={self.llm_base_url!r}, "
            f"llm_model={self.llm_model!r}, "
            f"webhook_url={self.webhook_url!r}, "
            f"port={self.port!r}, "
            f"data_dir={self.data_dir!r}, "
            f"mock={self.mock!r})"
        )


def load_settings() -> Settings:
    """Resolve settings: VSS_* env, else INGRESS/USERNAME/PASSWORD, else /config."""
    file_values: dict[str, str] | None = None

    def from_file(key: str) -> str:
        nonlocal file_values
        if file_values is None:
            file_values = _load_config_map()
        return file_values.get(key, "").strip()

    vss_url = _env("VSS_URL") or _env("INGRESS_URL") or from_file("VSS_URL") or from_file("INGRESS_URL")
    vss_username = (
        _env("VSS_USERNAME") or _env("USERNAME") or from_file("VSS_USERNAME") or from_file("USERNAME")
    )
    vss_password = (
        _env("VSS_PASSWORD") or _env("PASSWORD") or from_file("VSS_PASSWORD") or from_file("PASSWORD")
    )
    wandb_api_key = _env("WANDB_API_KEY") or from_file("WANDB_API_KEY")
    return Settings(
        vss_url=vss_url,
        vss_username=vss_username,
        vss_password=vss_password,
        wandb_api_key=wandb_api_key,
        wandb_project_path=_project_path(from_file),
        llm_model=_env("LLM_MODEL") or from_file("LLM_MODEL"),
        webhook_url=_env("ALERT_WEBHOOK_URL") or from_file("ALERT_WEBHOOK_URL"),
        port=_port(_env("PORT") or from_file("PORT")),
        data_dir=_env("DATA_DIR") or from_file("DATA_DIR") or "/tmp/alert-builder",
        mock=(_env("MOCK") or from_file("MOCK")).lower() in _TRUE,
    )


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _project_path(from_file) -> str:
    direct = _env("WANDB_PROJECT_PATH")
    if direct:
        return direct
    team, project = _env("WANDB_TEAM"), _env("WANDB_PROJECT")
    if team and project:
        return f"{team}/{project}"
    direct = from_file("WANDB_PROJECT_PATH")
    if direct:
        return direct
    team, project = from_file("WANDB_TEAM"), from_file("WANDB_PROJECT")
    if team and project:
        return f"{team}/{project}"
    return ""


def _port(value: str) -> int:
    if not value:
        return 8080
    try:
        return int(value)
    except ValueError:
        raise ValueError("invalid PORT") from None


def _load_config_map() -> dict[str, str]:
    if not CONFIG_DIR or not os.path.isdir(CONFIG_DIR):
        return {}
    paths = [
        path
        for path in sorted(glob.glob(os.path.join(CONFIG_DIR, "*.config")))
        if os.path.isfile(path)
    ]
    if not paths:
        return {}
    if len(paths) != 1:
        raise ValueError(f"expected one *.config file in {CONFIG_DIR}, found {len(paths)}")
    return _parse_config_file(paths[0])


def _parse_config_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export ") or (line.startswith("export") and line[6:7].isspace()):
                line = line[6:].strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
    return values
