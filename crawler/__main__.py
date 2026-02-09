"""Allow running as `python -m crawler`."""

import sys

if "--gui" in sys.argv:
    sys.argv.remove("--gui")
    from .gui import main
    main()
else:
    from .cli import main
    main()
