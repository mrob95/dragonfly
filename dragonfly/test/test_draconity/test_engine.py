import sys

if sys.version_info < (3,):
    from Queue import Queue
else:
    from queue import Queue

from nose.tools import eq_, assert_raises
import toml
from parameterized import parameterized

from dragonfly.engines.backend_draconity import engine

from dragonfly.test.mock_proxy import Mock, MagicMock, patch


class Test_DraconityConfig(object):
    # `_load_config_file` is implicitly tested by the integration tests.

    good_socket = """
        [[socket]]
        host = "localhost"
        port = 20000
    """
    empty_socket = """
        [[socket]]
    """
    no_socket = ""
    bad_socket_form = """
        [socket]
        host = "localhost"
        port = 20000
    """

    @parameterized(
        [
            # No socket format should throw an error.
            [good_socket, "localhost", 20000],
            [empty_socket, None, None],
            [no_socket, None, None],
            [bad_socket_form, None, None],
        ]
    )
    def test__extract_tcp_host(self, toml_object, target_host, target_port):
        toml_config = toml.loads(toml_object)
        host, port = engine._DraconityConfig._extract_tcp_host(toml_config)
        eq_(host, target_host)
        eq_(port, target_port)

    good_pipe = """
        [[pipe]]
        name = "dummy_pipe"
    """
    empty_pipe = """
        [[pipe]]
    """
    no_pipe = ""
    bad_pipe_form = """
        [pipe]
        name = "dummy pipe"
    """

    @parameterized(
        [
            [good_pipe, "dummy_pipe"],
            [empty_pipe, None],
            [no_pipe, None],
            [bad_pipe_form, None],
        ]
    )
    def test__extract_pipe_name(self, toml_object, target_pipe_name):
        toml_config = toml.loads(toml_object)
        eq_(target_pipe_name, engine._DraconityConfig._extract_pipe_name(toml_config))

    def test__extract_secret(self):
        toml_config = toml.loads('secret = "dummy_secret"')
        eq_("dummy_secret", engine._DraconityConfig._extract_secret(toml_config))

    def test__extract_secret_no_secret(self):
        toml_config = toml.loads("")
        assert_raises(ValueError, engine._DraconityConfig._extract_secret, toml_config)

    def test__load_from_disk(self):
        # Just a unit test. Integration tests will implicitly test actual disk
        # loading.
        with patch.object(
            engine._DraconityConfig,
            "_load_info_from_disk",
            return_value=("secret", "pipe_name", "tcp_host", "tcp_port"),
        ):
            config = engine._DraconityConfig.load_from_disk()
        eq_(config.secret, "secret")
        eq_(config.pipe_name, "pipe_name")
        eq_(config.tcp_host, "tcp_host")
        eq_(config.tcp_port, "tcp_port")

    pipe_and_socket = """
        secret = "secret"

        [[socket]]
        host = "host"
        port = "port"

        [[pipe]]
        name = "pipe"
    """
    no_socket = """
        secret = "secret"

        [[pipe]]
        name = "pipe"
    """
    no_pipe = """
        secret = "secret"

        [[socket]]
        host = "host"
        port = "port"
    """

    @parameterized(
        [
            [pipe_and_socket, "secret", "pipe", "host", "port"],
            [no_socket, "secret", "pipe", None, None],
            [no_pipe, "secret", None, "host", "port"],
        ]
    )
    def test__load_info_from_disk_success(
        self, dummy_config, target_secret, target_pipe, target_host, target_port
    ):
        with patch.object(
            engine._DraconityConfig,
            "_load_config_file",
            return_value=toml.loads(dummy_config),
        ):
            secret, pipe, host, port = engine._DraconityConfig._load_info_from_disk()
            eq_(secret, target_secret)
            eq_(pipe, target_pipe)
            eq_(host, target_host)
            eq_(port, target_port)

    no_port_or_socket = """
        secret = "secret"
    """
    partial_socket = """
        secret = "secret"

        [[socket]]
        host = "host"
    """

    @parameterized([[no_port_or_socket], [partial_socket]])
    def test__load_from_disk_failure(self, bad_config):
        with patch.object(
            engine._DraconityConfig,
            "_load_config_file",
            return_value=toml.loads(bad_config),
        ):
            assert_raises(ValueError, engine._DraconityConfig._load_info_from_disk)

    def test__load_config_file_bad_path(self):
        # Only test valid load in integration tests.
        with patch.object(
            engine,
            "_draconity_config_path",
            # Nonsense path
            return_value="saljkjafljklsjfasadjf",
        ):
            assert_raises(IOError, engine._DraconityConfig._load_config_file)


class Test_FunctionLoop:
    def test_push_and_pop(self):
        """Check that functions can be queued and executed properly."""
        loop = engine._FunctionLoop()
        eq_(loop._queue.qsize(), 0)

        function_1 = Mock()
        loop.queue_function(function_1)
        eq_(loop._queue.qsize(), 1)
        function_2 = Mock()
        loop.queue_function(function_2)
        eq_(loop._queue.qsize(), 2)

        function_1.assert_not_called()
        function_2.assert_not_called()

        loop._pump_message(loop._queue)
        function_1.assert_called_once_with()
        function_2.assert_not_called()

        loop._pump_message(loop._queue)
        function_1.assert_called_once_with()
        function_2.assert_called_once_with()

    def test_queue_function_non_callable(self):
        loop = engine._FunctionLoop()
        non_callable = "not callable"
        assert_raises(ValueError, loop.queue_function, non_callable)

    def test_pump_messages(self):
        loop = engine._FunctionLoop()
        function_1 = Mock()
        function_2 = Mock()
        exit_function = MagicMock(side_effect=[engine._FunctionLoop.Finished])

        loop.queue_function(function_1)
        loop.queue_function(function_2)
        loop.queue_function(exit_function)
        function_1.assert_not_called()
        function_2.assert_not_called()
        exit_function.assert_not_called()

        loop.pump_messages()
        function_1.assert_called_once_with()
        function_2.assert_called_once_with()
        exit_function.assert_called_once_with()


class TestDraconityEngine:
    def test___init__(self):
        engine_instance = engine.DraconityEngine()
        assert isinstance(engine_instance._message_loop, engine._FunctionLoop)

    def test__do_recognition(self):
        with patch.object(
            engine._FunctionLoop,
            "pump_messages",
            side_effects=[engine._FunctionLoop.Finished],
        ):
            engine_instance = engine.DraconityEngine()
            engine_instance._do_recognition()
