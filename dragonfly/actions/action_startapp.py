﻿#
# This file is part of Dragonfly.
# (c) Copyright 2007, 2008 by Christo Butcher
# Licensed under the LGPL.
#
#   Dragonfly is free software: you can redistribute it and/or modify it
#   under the terms of the GNU Lesser General Public License as published
#   by the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   Dragonfly is distributed in the hope that it will be useful, but
#   WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   Lesser General Public License for more details.
#
#   You should have received a copy of the GNU Lesser General Public
#   License along with Dragonfly.  If not, see
#   <http://www.gnu.org/licenses/>.
#

"""
BringApp and StartApp actions
============================================================================

The :class:`StartApp` and :class:`BringApp` action classes are used to
start an application and bring it to the foreground.  :class:`StartApp`
starts an application by running an executable file, while
:class:`BringApp` first checks whether the application is already running
and if so brings it to the foreground, otherwise starts it by running the
executable file.


Example usage
----------------------------------------------------------------------------

The following example brings Notepad to the foreground if it is already
open, otherwise it starts Notepad::

   BringApp(r"C:\\Windows\\system32\\notepad.exe").execute()

Note that the path to *notepad.exe* given above might not be correct for
your computer, since it depends on the operating system and its
configuration.

In some cases an application might be accessible simply through the file
name of its executable, without specifying the directory.  This depends on
the operating system's path configuration.  For example, on the author's
computer the following command successfully starts Notepad::

   BringApp("notepad").execute()


Class reference
----------------------------------------------------------------------------

"""

import os.path
from subprocess           import Popen
import time

from six import string_types

from .action_base         import ActionBase, ActionError
from .action_focuswindow  import FocusWindow
from .action_waitwindow   import WaitWindow
from ..windows            import Window


#---------------------------------------------------------------------------

class StartApp(ActionBase):
    """
        Start an application.

        When this action is executed, it runs a file (executable),
        optionally with commandline arguments.

    """

    def __init__(self, *args, **kwargs):
        """
            Constructor arguments:
             - *args* (variable argument list of *str*'s) --
               these strings are passed to subprocess.Popen()
               to start the application as a child process
             - *cwd* (*str*, default *None*) --
               if not *None*, then start the application in this
               directory
             - *focus_after_start* (*bool*, default *False*) --
               if *True*, then attempt to bring the window to the foreground
               after starting the application.

            A single *list* or *tuple* argument can be used instead of
            variable arguments.

        """
        ActionBase.__init__(self)
        if len(args) == 1 and isinstance(args[0], (tuple, list)):
            args = args[0]  # use the sub-list instead

        self._args = args

        self._cwd = kwargs.pop("cwd", None)
        self._focus_after_start = kwargs.pop("focus_after_start", False)
        if kwargs:
            raise ActionError("Invalid keyword arguments: %r" % kwargs)

        # Expand any variables within path names.
        self._args = [self._interpret(a) for a in self._args]
        if self._cwd:
            self._cwd = self._interpret(self._cwd)

        self._str = str(", ".join(repr(a) for a in self._args))

    @classmethod
    def _interpret(cls, path):
        if not isinstance(path, string_types):
            raise TypeError("expected string argument for path, but got "
                            "%s" % path)

        return os.path.expanduser(os.path.expandvars(path))

    def _execute(self, data=None):
        self._log.debug("Starting app: %r", self._args)
        try:
            process = Popen(self._args, cwd=self._cwd)
        except Exception as e:
            raise ActionError("Failed to start app %s: %s" % (self._str, e))

        if self._focus_after_start:
            timeout = 1.0
            exe = self._args[0]
            action = WaitWindow(executable=exe, timeout=timeout)
            if action.execute():
                # Bring the window to the foreground.
                Window.get_foreground().set_foreground()
            else:
                target = process.pid
                start = time.time()
                while time.time() - start < timeout:
                    found = False
                    for window in Window.get_matching_windows(exe):
                        if window.pid == target:
                            window.set_foreground()
                            found = True
                            break
                    if found:
                        break


#---------------------------------------------------------------------------

class BringApp(StartApp):
    """
        Bring an application to the foreground, starting it if it is not
        yet running.

        When this action is executed, it looks for an existing window of
        the application specified in the constructor arguments.  If an
        existing window is found, that window is brought to the
        foreground.  On the other hand, if no window is found the
        application is started.

        Note that the constructor arguments are identical to those used by
        the :class:`StartApp` action class.

    """

    def __init__(self, *args, **kwargs):
        """
            Constructor arguments:
             - *args* (variable argument list of *str*'s) --
               these strings are passed to :meth:`subprocess.Popen`
               to start the application as a child process
             - *cwd* (*str*, default *None*) --
               if not *None*, then start the application in this
               directory
             - *title* (*str*, default *None*) --
               if not *None*, then match existing windows using this
               title.
             - *index* (*str* or *int*) -- zero-based index of the target
               window, for multiple matching windows; can be a string (for
               substitution) but must be convertible to an integer.
             - *filter_func* (*callable*) -- called with a single argument
               (the window object), and should return ``True`` for your
               target windows; example:
               ``lambda window: window.get_position().dy > 100``.
             - *focus_after_start* (*bool*, default *False*) --
               if *True*, then attempt to bring the window to the foreground
               after starting the application. Does nothing if the
               application is already running.

        """
        self._title = kwargs.pop("title", None)
        self._index = kwargs.pop("index", None)
        self._filter_func = kwargs.pop("filter_func", None)
        StartApp.__init__(self, *args, **kwargs)

    def _execute(self, data=None):
        self._log.debug("Bringing app: %r", self._args)
        target = self._args[0].lower()
        title = self._title
        index = self._index
        filter_func = self._filter_func
        focus_action = FocusWindow(executable=target, title=title,
                                   index=index, filter_func=filter_func)
        # Attempt to focus on an existing window.
        if not focus_action.execute():
            # Failed to focus on an existing window, so start
            #  the application.
            StartApp._execute(self, data)
