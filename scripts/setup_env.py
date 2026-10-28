"""Create local configuration with a generated DB password; never overwrite an existing .env."""
from pathlib import Path
import secrets


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    if target.exists():
        print(".env already exists; keeping existing configuration.")
        return
    text = (root / ".env.example").read_text(encoding="utf-8")
    text = text.replace("POSTGRES_PASSWORD=", "POSTGRES_PASSWORD=" + secrets.token_hex(24), 1)
    with target.open("x", encoding="utf-8") as file:
        file.write(text)
    target.chmod(0o600)
    print("Created .env with a generated local PostgreSQL password.")


if __name__ == "__main__":
    main()
