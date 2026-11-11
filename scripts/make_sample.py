"""Write samples/sample.pdf, the small styled document used during development."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.fixtures import styled_text  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "samples/sample.pdf")
out.write_bytes(styled_text())
print(f"wrote {out}")
