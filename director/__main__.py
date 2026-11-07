from director.server import serve
from pathlib import Path
import os


def main():
    root = Path(__file__).resolve().parents[1]
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
    )


if __name__ == "__main__":
    main()
