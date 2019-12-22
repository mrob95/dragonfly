import itertools

import bson
from nose.tools import eq_, assert_raises

from dragonfly.engines.backend_draconity import client, stream
from dragonfly.test.mock_proxy import Mock, MagicMock, patch


class Test_DRACONITY_HEADER_STRUCT:
    """Test that the header struct correctly represents draconity headers."""

    def test_pack(self):
        eq_(
            client._DRACONITY_HEADER_STRUCT.pack(4020, 200),
            b"\x00\x00\x0f\xb4\x00\x00\x00\xc8",
        )

    def test_unpack(self):
        eq_(
            client._DRACONITY_HEADER_STRUCT.unpack(b"\x00\x00\x0f\xb4\x00\x00\x00\xc8"),
            (4020, 200),
        )


def _ensure_join(thread, timeout=0.1):
    """Try to join a thread. Throw an error if it times out."""
    thread.join(timeout)
    if thread.isAlive():
        raise RuntimeError("Could not join thread within timeout.")


class TestDraconityClient:
    def setup(self):
        self.on_message = Mock()
        self.on_error = Mock()
        self.on_disconnect = Mock()
        self.client = client.DraconityClient(
            self.on_message, self.on_error, self.on_disconnect
        )
        self.dummy_stream = Mock()

    def teardown(self):
        pass

    def test___init__(self):
        eq_(self.client._on_message, self.on_message)
        eq_(self.client._on_error, self.on_error)
        eq_(self.client._on_disconnect, self.on_disconnect)

        eq_(self.client._stream, None)
        eq_(self.client._receiver, None)

        eq_(self.client._deliberately_closed, None)

        # `_tid_counter` has its own test.

    def test_connect(self):
        assert not self.client._receiver

        self.client.connect(self.dummy_stream)
        eq_(self.client._stream, self.dummy_stream)
        assert not self.client._deliberately_closed
        assert self.client._receiver.isAlive()

    def test_connect_connected(self):
        with patch.object(client.DraconityClient, "connected", True):
            assert_raises(RuntimeError, self.client.connect, self.dummy_stream)

    def test__recv_messages(self):
        self.client._deliberately_closed = True  # Hacky
        self.client._pump_one_message = MagicMock(
            side_effect=[
                # Simulate two messages, then a close.
                (1, {"dummy", "message"}),
                (2, {"another", "message"}),
                RuntimeError,
            ]
        )
        self.client.handle_message = Mock()

        self.client._recv_messages()
        eq_(self.client._pump_one_message.call_count, 3)

    def test__recv_messages_error(self):
        # All errors should be handled when pumping - not just problems with
        # the stream.
        target_error = IndexError("Dummy error that isn't stream-related.")
        self.client._pump_one_message = MagicMock(side_effect=[target_error])
        self.client._handle_error = Mock()
        assert not self.client._deliberately_closed

        self.client._recv_messages()
        self.client._handle_error.assert_called_once_with(target_error)

    def test__pump_one_message(self):
        dummy_tid = 123
        dummy_size = 456
        dummy_header = (dummy_tid, dummy_size)
        dummy_body = "dummy body"
        self.client._receive_header = Mock(return_value=dummy_header)
        self.client._receive_body = Mock(return_value=dummy_body)

        eq_(self.client._pump_one_message(), (dummy_tid, dummy_body))
        self.client._receive_body.assert_called_once_with(dummy_size)

    def test__receive_header(self):
        header_size = 8
        self.dummy_stream.recv = Mock(return_value=b"\x00\x00\x0f\xb4\x00\x00\x00\xc8")
        self.client._stream = self.dummy_stream

        eq_(self.client._receive_header(), (4020, 200))
        self.dummy_stream.recv.assert_called_once_with(header_size)

    def test__receive_body(self):
        dummy_body = {"dummy": "body"}
        dummy_size = 12345
        self.dummy_stream.recv = Mock(return_value=bson.BSON.encode(dummy_body))
        self.client._stream = self.dummy_stream

        eq_(self.client._receive_body(dummy_size), dummy_body)
        self.dummy_stream.recv.assert_called_once_with(dummy_size)

    def test__handle_error(self):
        dummy_error = Exception("Just a dummy error")
        self.client._handle_error(dummy_error)
        self.client._on_error.assert_called_once_with(dummy_error)

    def test__handle_error_no_callable(self):
        self.client._on_error = None
        self.client._handle_error(Exception("Dummy error"))

    def test__handle_error_erroneous_callback(self):
        # Keep output clean.
        self.client._communicate_exception = Mock()
        self.client._on_error = MagicMock(side_effect=[Exception])
        self.client._handle_error(Exception("Dummy error"))

    def test__handle_disconnect(self):
        self.client._handle_disconnect()
        self.client._on_disconnect.assert_called_once()

    def test__handle_disconnect_no_callable(self):
        self.client._on_disconnect = None
        self.client._handle_disconnect()

    def test__handle_disconnect_erroneous_callback(self):
        # Keep output clean.
        self.client._communicate_exception = Mock()
        self.client._on_disconnect = MagicMock(side_effect=[Exception])
        self.client._handle_disconnect()

    def test__handle_message(self):
        dummy_tid = 1020
        dummy_message = {"dummy": "message"}

        self.client._handle_message(dummy_tid, dummy_message)
        self.client._on_message.assert_called_once_with(dummy_tid, dummy_message)

    def test__handle_message_erroneous_callback(self):
        # Keep output clean.
        self.client._communicate_exception = Mock()
        self.client._on_message = MagicMock(side_effect=[Exception])
        self.client._handle_message(110, {"dummy": "message"})

    def test_close(self):
        self.client._safely_close_stream = Mock()

        self.client.connect(Mock())
        assert not self.client._deliberately_closed
        assert self.client.connected
        self.client._safely_close_stream.assert_not_called()

        self.client.close()
        assert self.client._deliberately_closed
        self.client._safely_close_stream.assert_called()

    def test__safely_close_stream(self):
        self.dummy_stream.close = MagicMock(side_effect=[Exception])
        self.client._stream = self.dummy_stream

        self.client._safely_close_stream()
        self.dummy_stream.close.assert_called_once()

    @staticmethod
    def _connect_then_disconnect(client):
        # Connect
        dummy_stream = Mock()
        dummy_stream.close = Mock()
        client.connect(dummy_stream)
        eq_(client._stream, dummy_stream)
        assert client.connected

        # Disconnect
        client.close()
        eq_(client._stream, None)
        assert not client.connected
        _ensure_join(client._receiver, 1)

    def test_can_reconnect(self):
        """Ensure we can close & reconnect repeatedly."""
        self._connect_then_disconnect(self.client)
        self._connect_then_disconnect(self.client)
        self._connect_then_disconnect(self.client)

    def test_connected(self):
        # TODO: Maybe try for race conditions?
        assert not self.client.connected
        self.client.connect(Mock())
        assert self.client.connected
        self.client.close()
        assert not self.client.connected

    def test_send(self):
        self.client._stream = Mock()
        self.client._stream.send = Mock()
        # Check for a distinct tid, not just 0
        self.client._tid_counter = iter([923])

        tid = self.client.send({"dummy": "message"})
        eq_(tid, 923)
        encoded_message = b"\x00\x00\x03\x9b\x00\x00\x00\x18\x18\x00\x00\x00\x02dummy\x00\x08\x00\x00\x00message\x00\x00"
        self.client._stream.send.assert_called_once_with(encoded_message)

    def test_send_not_connected(self):
        eq_(self.client._stream, None)
        assert_raises(stream.StreamError, self.client.send, {"dummy": "message"})

    def test__tid_counter(self):
        first_five_tids = list(itertools.islice(self.client._tid_counter, 5))
        # TIDs start at 1, NOT 0.
        eq_(first_five_tids, [1, 2, 3, 4, 5])

    def test__prep_message(self):
        tid, prepped_message = self.client._prep_message({"dummy": "message"})
        eq_(tid, 1)
        eq_(
            prepped_message,
            b"\x00\x00\x00\x01\x00\x00\x00\x18\x18\x00\x00\x00\x02dummy\x00\x08\x00\x00\x00message\x00\x00",
        )

        tid, prepped_message = self.client._prep_message({"another": "one"})
        eq_(tid, 2)
        eq_(
            prepped_message,
            b"\x00\x00\x00\x02\x00\x00\x00\x16\x16\x00\x00\x00\x02another\x00\x04\x00\x00\x00one\x00\x00",
        )
