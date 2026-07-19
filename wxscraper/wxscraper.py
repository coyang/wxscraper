"""Fallback module launcher for `python -m wxscraper` inside package dir.

If users accidentally `cd wxscraper/wxscraper` and run
`python -m wxscraper`, Python resolves this file instead of the real
package. We bootstrap the actual package from the current directory and
delegate to `wxscraper.cli:main`.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent


def _load_real_package() -> None:
    init_file = PACKAGE_DIR / "__init__.py"
    spec = spec_from_file_location(
        "wxscraper",
        init_file,
        submodule_search_locations=[str(PACKAGE_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load package spec from {init_file}")

    module = module_from_spec(spec)
    sys.modules["wxscraper"] = module
    spec.loader.exec_module(module)


def main() -> int:
    _load_real_package()
    from wxscraper.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
