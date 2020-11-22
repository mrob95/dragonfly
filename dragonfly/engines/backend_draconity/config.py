import toml
import os
import platform
import secrets
import logging

_log = logging.getLogger("engine")

def _draconity_config_path():
    """Get the path to the Draconity config file, based on platform.

    Note this does not mean the file actually exists.

    :returns str: expanded path to the config file.

    """
    system = platform.system()
    if system in ["Darwin", "Linux"]:
        return os.path.expanduser("~/.talon/draconity.toml")
    elif system == "Windows":
        return os.path.expandvars("%APPDATA%\\talon\\draconity.toml")
    else:
        raise NotImplementedError("Not supported on this platform.")

def _draconity_default_config():
    target_directory = os.path.dirname(_draconity_config_path())
    log_path = os.path.join(target_directory, "draconity.log")
    cfg = {
        "timeout": 40,
        "timeout_incomplete": 500,
        "prevent_wake": 1,
        "secret": secrets.token_urlsafe(16),
        "logfile": log_path,
        "socket": [
            {
                "host": "127.0.0.1",
                "port": 38065,
            }
        ]
    }
    return cfg

def _create_config_file(target_path):
    target_directory = os.path.dirname(target_path)
    if os.path.exists(target_path):
        raise ValueError("Attempting to create '%s', but the file already exists.", target_path)
    if not os.path.isdir(target_directory):
        _log.info("Creating directory '%s'.", target_directory)
        os.makedirs(target_directory)
    with open(target_path, "w+") as f:
        _log.info("Creating draconity.toml in '%s'.", target_directory)
        toml.dump(_draconity_default_config(), f)


class _DraconityConfig(object):

    def __init__(self, secret, pipe_path, tcp_host, tcp_port):
        """Create an object that holds the current Draconity config."""
        self.pipe_path = pipe_path
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.secret = secret

    def __repr__(self):
        return "_DraconityConfig(secret='%s', pipe_path=%s, tcp_host='%s', tcp_port=%s)" % self.secret, self.pipe_path, self.tcp_host, self.tcp_port

    @staticmethod
    def _load_config_file():
        """Load Draconity's config as a dict from the config file, creating it if necessary."""
        config_path = _draconity_config_path()
        if not os.path.exists(config_path):
            _log.info("Draconity config file not found, creating...")
            _create_config_file(config_path)
        _log.info("Loading draconity config from '%s'.", config_path)
        return toml.load(config_path)

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

    def assert_valid_connection(self):
        """Ensure there was at least one valid connection loaded."""
        self._assert_valid_connection(self.pipe_path, self.tcp_host, self.tcp_port)
