"""Enable `python -m plex_home` as the entrypoint."""
from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
