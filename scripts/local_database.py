"""Create a stable, private credential file for one local development profile."""

import json
import os
from pathlib import Path
import secrets
import sys


def ensure_credentials(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = path.with_name("terraform.tfstate")
    if not path.exists() and state.is_file():
        resources = json.loads(state.read_text()).get("resources", [])
        if any(resource.get("type") == "kubernetes_secret_v1" and resource.get("name") == "postgres" for resource in resources):
            raise ValueError(f"database credentials missing for existing database state; recover {path} rather than resetting its password")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        data = json.loads(path.read_text())
        password = data.get("postgres_password") if isinstance(data, dict) else None
        if not isinstance(password, str) or len(password) != 64 or any(character not in "0123456789abcdef" for character in password):
            raise ValueError(f"invalid local database credential file: {path}")
        path.chmod(0o600)
        return
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"postgres_password": secrets.token_hex(32)}, stream)
        stream.write("\n")


if __name__ == "__main__":
    try:
        ensure_credentials(Path(sys.argv[1]))
    except (OSError, ValueError) as error:
        raise SystemExit(f"ERROR local database credentials: {error}") from error
