import sys

if sys.version_info < (3,):
    from Queue import Queue
else:
    from queue import Queue

import os
import platform

import toml

from ..base import EngineBase


def _draconity_config_path():
    """Get the path to the Draconity config file, based on platform.

    Note this does not mean the file actually exists.

    :returns str: expanded path to the config file.

    """
    system = platform.system()
    if system in ["Darwin", "Linux"]:
        return os.path.expanduser("~/.talon/draconity.toml")
    elif system == "Windows":
        return os.path.expandvars("%APPDATA%/talon/draconity.toml")
    else:
        raise NotImplementedError("Not supported on this platform.")


class _DraconityConfig(object):
    def __init__(self, secret, pipe_path, tcp_host, tcp_port):
        """Create an object that holds the current Draconity config."""
        self.pipe_path = pipe_path
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.secret = secret

    @staticmethod
    def _load_config_file():
        """Load Draconity's config as a dict from the config file."""
        return toml.load(_draconity_config_path())

    @staticmethod
    def _extract_tcp_host(config):
        """Extract Draconity's TCP address from the toml `config`."""
        try:
            host = config["socket"][0]["host"]
        except (IndexError, KeyError, TypeError):
            host = None
        try:
            port = config["socket"][0]["port"]
        except (IndexError, KeyError, TypeError):
            port = None
        return host, port

    @staticmethod
    def _extract_pipe_path(config):
        """Extract the pipe name from the toml `config`."""
        try:
            return config["pipe"][0]["path"]
        except (IndexError, KeyError, TypeError):
            return None

    @staticmethod
    def _extract_secret(config):
        """Extract the secret from the toml `config`."""
        secret = config.get("secret")
        if secret:
            return secret
        else:
            raise ValueError("No secret defined in Draconity config.")

    @classmethod
    def load_from_disk(cls):
        secret, pipe_path, tcp_host, tcp_port = cls._load_info_from_disk()
        return cls(secret, pipe_path, tcp_host, tcp_port)

    @staticmethod
    def _load_info_from_disk():
        config_toml = _DraconityConfig._load_config_file()
        pipe_path = _DraconityConfig._extract_pipe_path(config_toml)
        tcp_host, tcp_port = _DraconityConfig._extract_tcp_host(config_toml)
        _DraconityConfig._assert_valid_connection(pipe_path, tcp_host, tcp_port)
        secret = _DraconityConfig._extract_secret(config_toml)
        return secret, pipe_path, tcp_host, tcp_port

    @staticmethod
    def _assert_valid_connection(pipe_path, tcp_host, tcp_port):
        """Ensure there was at least one valid connection loaded."""
        connection_defined = pipe_path or (tcp_host and tcp_port)
        if not connection_defined:
            raise ValueError(
                "Neither pipe nor full TCP socket defined in Draconity config."
            )


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
