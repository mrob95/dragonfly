from __future__ import print_function
import os.path
import logging
import os

from dragonfly import get_engine
from dragonfly.loader import CommandModuleDirectory


# --------------------------------------------------------------------------
# Main event driving loop.

def main():
    logging.basicConfig(level=logging.INFO)

    try:
        path = os.path.dirname(__file__)
    except NameError:
        # The "__file__" name is not always available, for example
        # when this module is run from PythonWin.  In this case we
        # simply use the current working directory.
        path = os.getcwd()
        __file__ = os.path.join(path, "kaldi_module_loader_plus.py")

    # Set any configuration options here as keyword arguments.
    engine = get_engine('draconity',
        injector_path="inject.exe",
        draconity_path="libdraconity.dll",
        dragon_old_version=False,
    )

    # Call connect() now that the engine configuration is set.
    engine.connect()

    directory = CommandModuleDirectory(path, excludes=[__file__])
    directory.load()

    # Start the engine's main recognition loop
    try:
        engine.do_recognition()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
