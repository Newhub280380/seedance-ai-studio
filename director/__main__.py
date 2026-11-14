from director.server import serve
from pathlib import Path
import os


def load_dotenv(path):
    """Read KEY=value lines from a local .env (gitignored). The environment wins over the file."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    data = Path(os.environ.get("DIRECTOR_DATA_DIR") or (root / "director-data"))
    host = os.environ.get("DIRECTOR_HOST") or "127.0.0.1"
    port = int(os.environ.get("DIRECTOR_PORT") or "8765")
    serve(
        root,
        data,
        host=host,
        port=port,
        venice_api_key=os.environ.get("VENICE_API_KEY") or "",
        minimax_api_key=os.environ.get("MINIMAX_API_KEY") or "",
        fal_api_key=os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or "",
    )


if __name__ == "__main__":
    main()
