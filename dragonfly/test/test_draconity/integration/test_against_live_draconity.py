"""Suite to test behavior against a live instance of Draconity.

This suite is designed to be run explicitly, not automatically. Draconity must
be running for these tests to work. The suite connects with the settings in
Draconity's config file. It's slow - it could take 20-30 seconds to run.

The objective is to ensure our assumptions about how to communicate with
Draconity & pass information to it are correct. If the API surface we rely on
changes, these tests should flag it and fail.

Note these tests must be run sequentially - they won't work in parallel. The
rapid simultaneous connections can confuse Draconity.

"""

import time
import pprint
import platform

from parameterized import parameterized_class


from dragonfly.engines.backend_draconity import client, engine, stream
from dragonfly.test.test_draconity.integration import _example_grammar
from dragonfly.test.mock_proxy import Mock


# The default time to wait for a response from Draconity.
DEFAULT_TIMEOUT = 5

_ON_WINDOWS = platform.system() == "Windows"
_draconity_config_cache = None


def _get_config():
    """Load Draconity's config (subsequent calls return the cached value)."""
    global _draconity_config_cache
    if not _draconity_config_cache:
        _draconity_config_cache = engine._DraconityConfig.load_from_disk()
    return _draconity_config_cache


def _assert_response_success(response):
    """Assert that a generic `response` was successful."""
    assert isinstance(response, dict), type(response)
    formatted_response = pprint.pformat(response)
    assert "success" in response, formatted_response
    assert response["success"], formatted_response


def _assert_status_success(response):
    """Assert that a response with a "status" field was successful.

    Some responses, like those to "w.set" and "g.set", have a different field
    to indicate success ("status" instead of "success"). We have to evaluate
    success for these responses differently.

    """
    assert isinstance(response, dict), type(response)
    formatted_response = pprint.pformat(response)
    assert "status" in response, formatted_response
    # Not "error" or "skipped".
    assert response["status"] == "success", formatted_response


class _ClientHelper(object):
    """Helper class for the DraconityClient.

    Primarily designed to allow synchronous calls to the asynchronous client.
    Send messages and wait for the async response before returning. Also tracks
    every message received.

    """

    def __init__(self, make_stream, auto_unpause=True):
        """Create a new helper to help `DraconityClient` tests.

        :param make_stream: function that returns a connected StreamBase
          implementation. This is separated so the helper can be used with any
          stream implementation.
        :type make_stream: callable()
        :param bool auto_unpause: Optional. Should the helper unpause the client
          immediately whenever a pause message comes in? Default is True.

        """
        self._make_stream = make_stream
        self.on_disconnect = Mock()
        self.client = client.DraconityClient(
            on_message=self._handle_message,
            on_error=self._raise_error,
            on_disconnect=self.on_disconnect,
        )

        self.messages = []

        self.auto_unpause = auto_unpause

    def __enter__(self):
        """Open a connection to Draconity (including authorization).

        If anything fails, raises an error.

        """
        config = _get_config()
        self.stream = self._make_stream()
        self.client.connect(self.stream)
        self.send_wait_success(client.prep_auth(config.secret))
        return self

    def __exit__(self, *_):
        """Close the open session."""
        self.client.close()

    def _handle_message(self, tid, message):
        self._store_message(tid, message)
        # Draconity won't unpause until we tell it to (or it times out) - we
        # want to be able to ignore this and unpause immediately for most of
        # these tests.
        if self.auto_unpause and message.get("topic") == "paused":
            self._unpause(self.client, message)

    def _store_message(self, tid, message):
        self.messages.append((tid, message))

    @staticmethod
    def _unpause(client_, message):
        assert "token" in message
        # Threading means we can't wait for the response.
        client_.send(client.prep_unpause(message["token"]))

    @staticmethod
    def _raise_error(error):
        # If there's an issue with the connection we want the test to fail, so
        # just throw it.
        raise error

    def wait_for_response(self, tid, timeout=None):
        """Wait for a response to the message with `tid`.

        Raises an error on timeout.

        """
        start_time = time.clock()
        while not self._timed_out(start_time, timeout):
            response = self._get_message_from_tid(self.messages, tid)
            if response:
                return response
            else:
                time.sleep(0.001)
        raise IOError(
            "Response to {} not received within timeout. Messages:\n"
            " {}".format(tid, pprint.pformat(self.messages))
        )

    def wait_for_message(self, filter_, timeout=None):
        """Wait for a message that matches a partial filter.

        :param dict filter_: Dict of target fields. If every field from the
          filter matches the corresponding field in the message, it counts as a
          match. Other fields are not considered.

        """
        start_time = time.clock()
        while not self._timed_out(start_time, timeout):
            message = self._get_target_message(self.messages, filter_)
            if message:
                return message
            else:
                time.sleep(0.001)
        raise IOError(
            "Message not received within timeout. Messages:\n {}".format(
                pprint.pformat(self.messages)
            )
        )

    @staticmethod
    def _timed_out(start_time, timeout):
        """Has an operation timed out?"""
        if timeout:
            end_time = start_time + timeout
            current_time = time.clock()
            return current_time > end_time
        else:
            return False

    @staticmethod
    def _get_message_from_tid(messages, tid):
        """Get the message with `tid` from `messages`.

        If it doesn't exist, returns None.

        """
        for (current_tid, message) in messages:
            if current_tid == tid:
                return message
        return None

    @classmethod
    def _get_target_message(cls, messages, filter_):
        """Get the message matching `filter_` from `messages`."""
        for (tid, message) in messages:
            if cls._partial_match(message, filter_):
                return message
        return None

    @staticmethod
    def _partial_match(message, filter_):
        """Does `message` match `filter_`?"""
        for key in filter_:
            if key not in message or message[key] != filter_[key]:
                return False
        return True

    def send_wait(self, message, timeout=DEFAULT_TIMEOUT):
        """Send a message and wait for the response.

        Raises an exception on timeout.

        :returns dict: the response.

        """
        tid = self.client.send(message)
        return self.wait_for_response(tid, timeout)

    def send_wait_success(self, message, timeout=DEFAULT_TIMEOUT):
        """Send a message, then ensure it returns a successful response.

        An exception will be raised if not (or if it times out).

        :returns dict: the response.

        """
        response = self.send_wait(message, timeout)
        _assert_response_success(response)
        return response


# TODO: Maybe test `_ClientHelper`?


class _TempMicState(object):
    """Context manager to do something with a temporary mic state."""

    def __init__(self, client_helper, state="on"):
        self._client_helper = client_helper
        self._temp_state = state

    def __enter__(self):
        """Set the mic to the target state."""
        self._client_helper.send_wait_success(
            client.prep_set_mic_state(self._temp_state)
        )
        return self

    def __exit__(self, *_):
        """Turn the mic off."""
        self._client_helper.send_wait_success(client.prep_set_mic_state("off"))


def _assert_unload_grammar(client_helper, grammar_name):
    tid = client_helper.client.send(client.prep_grammar_unload(grammar_name))
    # Trigger a pause so Draconity pushes the update internally.
    _trigger_pause(client_helper)
    _assert_status_success(client_helper.wait_for_response(tid))


class _TempGrammar(object):
    """Context manager to do something with a grammar temporarily loaded."""

    def __init__(self, client_helper, name, blob, active_rules, lists):
        self._client_helper = client_helper
        self._name = name
        self._blob = blob
        self._active_rules = active_rules
        self._lists = lists

    def __enter__(self):
        """Load the target grammar."""
        tid = self._client_helper.client.send(
            client.prep_grammar_set(
                self._name, self._blob, self._active_rules, self._lists
            )
        )
        # Trigger a pause so Draconity pushes the update internally.
        _trigger_pause(self._client_helper)
        _assert_status_success(self._client_helper.wait_for_response(tid))
        return self

    def __exit__(self, *_):
        """Unload the target grammar."""
        _assert_unload_grammar(self._client_helper, self._name)


def _trigger_pause(client_helper):
    """Trigger a pause in Draconity."""
    # Hacky method to trigger a pause - mimic a nonsense phrase that we know
    # will fail.
    with _TempMicState(client_helper, "on"):
        client_helper.client.send(client.prep_mimic(["da;sg'dg';lsgl;sglsglsdg"]))


def _full_mimic(client_helper, phrase, mic_state="on"):
    """Perform a mimic and wait for it to complete successfully.

    Note `client_helper` must have `auto_unpause` enabled.

    """
    with _TempMicState(client_helper, mic_state):
        client_helper.send_wait_success(client.prep_mimic(phrase.split()), 2)


def _make_tcp_stream(*_):
    config = _get_config()
    config_tuple = (config.tcp_host, config.tcp_port)
    assert isinstance(config.tcp_host, (str, unicode)), config_tuple
    assert isinstance(config.tcp_port, int), config_tuple
    return stream.TCPStream(config.tcp_host, config.tcp_port)


def _make_windows_pipe_stream(*_):
    # Platform-dependent import - do it locally
    from dragonfly.engines.backend_draconity import windows_pipe

    config = _get_config()
    assert isinstance(config.pipe_path, (str, unicode)), config.pipe_path
    return windows_pipe.PipeStream(config.pipe_path)


class TestBasicConnection(object):
    """Test basic connection functionality for each stream."""

    def test_tcp_connection(self):
        tcp_stream = _make_tcp_stream()
        tcp_stream.close()

    if _ON_WINDOWS:

        def test_windows_pipe_connection(self):
            pipe_stream = _make_windows_pipe_stream()
            pipe_stream.close()


streams_to_test = [{"make_stream": _make_tcp_stream}]
if _ON_WINDOWS:
    streams_to_test.append({"make_stream": _make_windows_pipe_stream})


@parameterized_class(streams_to_test)
class TestMessagesWork(object):
    """Test our messages can manipulate a live Draconity instance."""

    def test_set_mic_state(self):
        with _ClientHelper(self.make_stream) as c:
            c.send_wait_success(client.prep_set_mic_state("on"))
            c.send_wait_success(client.prep_set_mic_state("sleeping"))
            c.send_wait_success(client.prep_set_mic_state("off"))

    @staticmethod
    def _recognition_on_grammar(client_helper, grammar_name, phrase):
        """Ensure a recognition recognises against a particular grammar."""
        with _TempMicState(client_helper, "on"):
            _full_mimic(client_helper, phrase)
            client_helper.wait_for_message(
                {"cmd": "p.end", "grammar": grammar_name, "phrase": phrase.split()}
            )

    def test_grammar_set(self):
        """Ensure we can set a grammar and perform recognitions against it."""
        with _ClientHelper(self.make_stream) as c:
            with _TempGrammar(
                c,
                _example_grammar.name,
                _example_grammar.blob_binary,
                _example_grammar.active_rules,
                _example_grammar.lists,
            ):
                self._recognition_on_grammar(
                    c, _example_grammar.name, "I want to eat an apple"
                )
                self._recognition_on_grammar(
                    c, _example_grammar.name, "I like to drink beer"
                )

    def test_grammar_unload(self):
        """Test that we can successfully queue grammar unloads.

        Draconity works on the principle of asserting state, so we don't need
        to check this against a grammar that's actually loaded.

        """
        with _ClientHelper(self.make_stream) as c:
            _assert_unload_grammar(c, "grammar_that_does_not_exist_1208947")

    @staticmethod
    def _list_words(client_helper):
        """Get the active words from Draconity.

        This can be a slow operation.

        """
        # Prepping words can take a while, so we use a long Timeout.
        response = client_helper.send_wait_success(client.prep_words_list(), 10)
        return response.get("words")

    def test_words_list(self):
        with _ClientHelper(self.make_stream) as c:
            word_list = self._list_words(c)
            # We can't check the word list directly, so just check it looks
            # like a real word list.
            assert isinstance(word_list, list), word_list
            assert len(word_list) > 100, word_list

    def test_words_set(self):
        with _ClientHelper(self.make_stream) as c:
            new_words = ["oflafupqwutqp", "saofiusiufaof", "saklffjaflj"]

            # Double check our nonsense words aren't loaded when we begin.
            loaded_words = self._list_words(c)
            for new_word in new_words:
                assert new_word not in loaded_words, new_word

            # Now we can set our words.
            tid = c.client.send(client.prep_word_set(new_words))
            # Trigger a pause so Draconity pushes the update internally.
            _trigger_pause(c)
            _assert_status_success(c.wait_for_response(tid))

            # Don't strictly need to verify (we aren't testing Dragon's
            # internals) but check anyway.
            loaded_words = self._list_words(c)
            for new_word in new_words:
                assert new_word in loaded_words, new_word

    def test_mimic(self):
        with _ClientHelper(self.make_stream) as c:
            _full_mimic(c, "wake up", mic_state="sleeping")

    def test_unpause(self):
        with _ClientHelper(self.make_stream, auto_unpause=False) as c:
            # Trigger a pause by mimicking nonsense on a sleeping mic
            with _TempMicState(c, "sleeping"):
                c.client.send(client.prep_mimic(["280'24;2142148`"]))

                # Manually wait for the pause to come in.
                pause_message = c.wait_for_message({"topic": "paused"})
                pause_token = pause_message["token"]

                # Now we can test unpause.
                c.send_wait_success(client.prep_unpause(pause_token))
