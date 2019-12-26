import sys

if sys.version_info < (3,):
    from Queue import Queue
else:
    from queue import Queue

from ..base import EngineBase


class _FunctionLoop(object):
    """Message loop that allows functions to be queued."""

    def __init__(self):
        self._queue = Queue()

    def queue_function(self, func, *args, **kwargs):
        """Push a function onto the queue."""
        if not callable(func):
            raise ValueError("Func must be callable, was: {}".format(type(func)))
        self._queue.put(lambda: func(*args, **kwargs))

    def pump_messages(self):
        """Repeatedly execute queued functions until one raises `Finished`.

        """
        try:
            while True:
                self._pump_message(self._queue)
        except _FunctionLoop.Finished:
            pass

    @staticmethod
    def _pump_message(queue):
        """Pop a function (wait until one is available), then execute it."""
        func = queue.get()
        return func()

    class Finished(Exception):
        """Raise this to break out of a message loop."""


class DraconityEngine(EngineBase):
    """Draconity-based engine backend."""

    _name = "draconity"

    def __init__(self):
        self._message_loop = _FunctionLoop()
        super(DraconityEngine, self).__init__()

    def connect(self):
        raise NotImplementedError("Not yet implemented.")

    def disconnect(self):
        raise NotImplementedError("Not yet implemented.")

    def _load_grammar(self, grammar):
        raise NotImplementedError("Not yet implemented.")

    def _unload_grammar(self, grammar):
        raise NotImplementedError("Not yet implemented.")

    def update_list(self, lst, grammar):
        raise NotImplementedError("Not yet implemented.")

    # TODO: Do we need these? Can we just discard them?
    def activate_grammar(self, grammar):
        # Rules are managed individually - no activation at the grammar level.
        pass

    def deactivate_grammar(self, grammar):
        # Rules are managed individually - no deactivation at the grammar
        # level.
        pass

    def activate_rule(self, rule, grammar):
        raise NotImplementedError("Not yet implemented.")

    def deactivate_rule(self, rule, grammar):
        raise NotImplementedError("Not yet implemented.")

    def set_exclusiveness(self, grammar, exclusive):
        raise NotImplementedError("Not yet implemented.")

    def mimic(self, words):
        """Mimic a recognition of the given `words`.

        :param list words: list of words to mimic.

        """
        raise NotImplementedError("Not yet implemented.")

    def speak(self, text):
        """Speak the given `text` using text-to-speech."""
        # TODO: Defer to a default TTS interface?
        raise NotImplementedError("Draconity does not support text-to-speech.")

    def _do_recognition(self):
        self._message_loop.pump_messages()

    # TODO: Language features? `_get_language`?
