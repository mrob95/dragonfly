"""Version-independent proxy for `mock` module imports.

`mock` is an external package before Python 3.3, built into `unittest` above.
Boilerplate to differentiate is collected here.

"""

import sys

if sys.version_info < (3, 3):
    from mock import Mock, MagicMock, patch
else:
    from unittest.mock import Mock, MagicMock, patch
