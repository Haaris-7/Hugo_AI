from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hugo.setup import read_env, write_env


def test_write_env_is_private_and_preserves_existing_values():
    with TemporaryDirectory(dir="/tmp") as directory:
        env_file = Path(directory) / ".env"
        write_env({"HUGO_API_TOKEN": "first", "UNRELATED": "keep"}, env_file)
        write_env({"HUGO_API_TOKEN": "second"}, env_file)

        assert read_env(env_file) == {"HUGO_API_TOKEN": "second", "UNRELATED": "keep"}
        assert env_file.stat().st_mode & 0o777 == 0o600


def test_write_env_rejects_newline_injection(tmp_path: Path):
    with pytest.raises(ValueError, match="cannot contain newlines"):
        write_env(
            {"HUGO_API_TOKEN": "safe\nHUGO_STRIPE_SECRET_KEY=injected"},
            tmp_path / ".env",
        )
