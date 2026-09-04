"""Configuration loading.

The path layout is deliberately identical on a local test box and on a real TLJH
install; only the tool binaries in ``[tools]`` differ between the two.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/opt/tljh/config/environ.toml")
SECRET_ENV_VAR = "GITHUB_WEBHOOK_SECRET"
ADMIN_TOKEN_ENV_VAR = "ENVIRON_ADMIN_TOKEN"
GITHUB_APP_ID_ENV_VAR = "GITHUB_APP_ID"
GITHUB_APP_INSTALLATION_ID_ENV_VAR = "GITHUB_APP_INSTALLATION_ID"
GITHUB_APP_PRIVATE_KEY_FILE_ENV_VAR = "GITHUB_APP_PRIVATE_KEY_FILE"
CONFIG_PATH_ENV_VAR = "ENVIRON_CONFIG"


class ConfigError(Exception):
    """Raised when the configuration file is missing or malformed."""


@dataclass(frozen=True)
class RepoConfig:
    url: str = ""
    branch: str = "main"
    path: Path = Path("/opt/tljh/environ/repo")
    ssh_key: Path | None = None

    @property
    def ref(self) -> str:
        return f"refs/heads/{self.branch}"


@dataclass(frozen=True)
class PathsConfig:
    env_root: Path = Path("/opt/tljh/user/envs")
    kernel_prefix: Path = Path("/opt/tljh/user")
    state_dir: Path = Path("/opt/tljh/environ/state")
    log_dir: Path = Path("/opt/tljh/environ/logs")

    @property
    def kernel_dir(self) -> Path:
        return self.kernel_prefix / "share" / "jupyter" / "kernels"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "environments.json"


@dataclass(frozen=True)
class ToolsConfig:
    git: str = "git"
    conda: str = "/opt/tljh/user/bin/mamba"
    uv: str = "/opt/tljh/user/bin/uv"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass(frozen=True)
class DefaultsConfig:
    python: str = "3.12"
    # "uv" builds requirements-*.txt venvs with uv; "pip" uses python -m venv + pip.
    installer: str = "uv"
    conda_channels: tuple[str, ...] = ("conda-forge",)
    # Directories (relative to the repo root) to scan for definition files.
    # An empty tuple means "the whole repo".
    search_roots: tuple[str, ...] = ()
    timeout_seconds: int = 3600


@dataclass(frozen=True)
class Config:
    repo: RepoConfig = field(default_factory=RepoConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    source: Path | None = None

    @property
    def webhook_secret(self) -> str | None:
        """Shared secret for signature checks. Never stored in the config file."""
        return os.environ.get(SECRET_ENV_VAR) or None

    @property
    def admin_token(self) -> str | None:
        """Bearer token guarding the manual rebuild endpoint."""
        return os.environ.get(ADMIN_TOKEN_ENV_VAR) or None

    @property
    def github_app_id(self) -> str | None:
        """GitHub App ID used to mint installation tokens for Check Runs."""
        return os.environ.get(GITHUB_APP_ID_ENV_VAR) or None

    @property
    def github_app_installation_id(self) -> str | None:
        """Installation ID of the App on the definitions repository."""
        return os.environ.get(GITHUB_APP_INSTALLATION_ID_ENV_VAR) or None

    @property
    def github_app_private_key_file(self) -> Path | None:
        """Path to the App private key PEM (mode 0600, not under /opt/tljh)."""
        value = os.environ.get(GITHUB_APP_PRIVATE_KEY_FILE_ENV_VAR)
        return Path(value).expanduser() if value else None


def _path(value: object, name: str) -> Path:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string path, got {type(value).__name__}")
    return Path(value).expanduser()


def _str_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    return tuple(value)


def _section(data: dict, name: str) -> dict:
    section = data.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] must be a table")
    return section


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration from TOML, falling back to the TLJH defaults."""
    if path is None:
        env_path = os.environ.get(CONFIG_PATH_ENV_VAR)
        path = Path(env_path) if env_path else DEFAULT_CONFIG_PATH
    path = Path(path).expanduser()

    if not path.exists():
        if path == DEFAULT_CONFIG_PATH:
            return Config()
        raise ConfigError(f"configuration file not found: {path}")

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    repo_data = _section(data, "repo")
    paths_data = _section(data, "paths")
    tools_data = _section(data, "tools")
    server_data = _section(data, "server")
    defaults_data = _section(data, "defaults")

    repo_defaults = RepoConfig()
    repo = RepoConfig(
        url=repo_data.get("url", repo_defaults.url),
        branch=repo_data.get("branch", repo_defaults.branch),
        path=_path(repo_data.get("path", str(repo_defaults.path)), "repo.path"),
        ssh_key=_path(repo_data["ssh_key"], "repo.ssh_key")
        if "ssh_key" in repo_data
        else None,
    )

    paths_defaults = PathsConfig()
    paths = PathsConfig(
        env_root=_path(
            paths_data.get("env_root", str(paths_defaults.env_root)), "paths.env_root"
        ),
        kernel_prefix=_path(
            paths_data.get("kernel_prefix", str(paths_defaults.kernel_prefix)),
            "paths.kernel_prefix",
        ),
        state_dir=_path(
            paths_data.get("state_dir", str(paths_defaults.state_dir)),
            "paths.state_dir",
        ),
        log_dir=_path(
            paths_data.get("log_dir", str(paths_defaults.log_dir)), "paths.log_dir"
        ),
    )

    tools_defaults = ToolsConfig()
    tools = ToolsConfig(
        git=tools_data.get("git", tools_defaults.git),
        conda=tools_data.get("conda", tools_defaults.conda),
        uv=tools_data.get("uv", tools_defaults.uv),
    )

    server_defaults = ServerConfig()
    server = ServerConfig(
        host=server_data.get("host", server_defaults.host),
        port=int(server_data.get("port", server_defaults.port)),
    )

    defaults_defaults = DefaultsConfig()
    installer = defaults_data.get("installer", defaults_defaults.installer)
    if installer not in {"uv", "pip"}:
        raise ConfigError(
            f"defaults.installer must be 'uv' or 'pip', got {installer!r}"
        )
    defaults = DefaultsConfig(
        python=str(defaults_data.get("python", defaults_defaults.python)),
        installer=installer,
        conda_channels=(
            _str_tuple(defaults_data["conda_channels"], "defaults.conda_channels")
            if "conda_channels" in defaults_data
            else defaults_defaults.conda_channels
        ),
        search_roots=(
            _str_tuple(defaults_data["search_roots"], "defaults.search_roots")
            if "search_roots" in defaults_data
            else defaults_defaults.search_roots
        ),
        timeout_seconds=int(
            defaults_data.get("timeout_seconds", defaults_defaults.timeout_seconds)
        ),
    )

    return Config(
        repo=repo,
        paths=paths,
        tools=tools,
        server=server,
        defaults=defaults,
        source=path,
    )
