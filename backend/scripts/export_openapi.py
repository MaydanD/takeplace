"""Export the FastAPI OpenAPI schema to a JSON file.

Run from the ``backend`` directory:

    python scripts/export_openapi.py ../frontend/openapi/openapi.json

This does not require a running server or a database: the schema is produced
directly from the app object. It is the first step of the reproducible
``FastAPI/OpenAPI -> TypeScript`` pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a plain script from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402


def build_schema() -> dict[str, object]:
    # Deterministic settings so the emitted schema never depends on the local
    # environment (docs URLs, etc. are all non-production here).
    settings = Settings(env="development")
    app = create_app(settings)
    return app.openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the Takeplace OpenAPI schema")
    parser.add_argument(
        "output",
        nargs="?",
        default="../frontend/openapi/openapi.json",
        help="Output path for the OpenAPI JSON document",
    )
    args = parser.parse_args(argv)

    schema = build_schema()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stable formatting keeps the committed artifact diff-friendly.
    output.write_text(json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote OpenAPI schema to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
