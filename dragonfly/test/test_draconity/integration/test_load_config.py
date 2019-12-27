# TODO: Probably want to move this to a larger file once the integration test
#   suite is written.

import os

from dragonfly.engines.backend_draconity import engine


def test_draconity_config_path_exists():
    if not os.path.isfile(engine._draconity_config_path()):
        raise IOError(
            "Could not find the Draconity config file. Ensure "
            'the ".talon" folder has been set up correctly.'
        )
