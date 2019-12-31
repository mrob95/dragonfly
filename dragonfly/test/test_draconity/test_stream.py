"""Functional tests for `stream.py` - specifically, the TCP stream.

These tests check the TCP stream against a dummy server. Fundamentally
`TCPStream` is just a wrapper around a TCP socket, so unit testing is not that
useful - better to check it against a live server.

"""


import socket
import threading
import time

from nose.tools import eq_, assert_raises

from dragonfly.engines.backend_draconity import stream


LOCALHOST = "localhost"


class _DummyServer(object):
    """TCP server that can be used to test clients against.

    The server will accept a connection from one client, receive a specified
    message, send a specified reply, then (optionally) disconnect from the
    client and die.

    """

    def __init__(self, message_to_recv=None, message_to_send=None, die_after=True):
        """Create a new dummy server.

        :param bytes message_to_recv: Optional. The message to receive, if any.
          Please make this small (below 2048 bytes) to ensure the entire
          message can be received in a single chunk. Default None.
        :param bytes message_to_send: Optional. The message to send, if any.
          This can be arbitrarily large. Default None.
        :param bool die_after: Should the server disconnect from the client &
          die after it's done?

        """
        self._message_to_recv = message_to_recv
        self._message_to_send = message_to_send
        self._die_after = die_after

        # When a message is received, it will be stored here.
        self.message_received = None

        self.address = LOCALHOST
        self._socket = socket.socket()
        # Use 0 so we don't worry about port clashes.
        self._socket.bind((self.address, 0))
        self._socket.listen(1)
        _, self.port = self._socket.getsockname()

        self._socket_thread = threading.Thread(target=self._handle_one_client)
        self._socket_thread.start()

    def kill_socket(self):
        """Force close the server socket."""
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except socket.error:
            pass
        self._socket.close()

    def wait_until_terminate(self):
        """Wait until the socket thread terminates."""
        self._socket_thread.join()

    def __del__(self):
        try:
            self.kill_socket()
        except Exception as e:
            pass

    def _handle_one_client(self):
        """Handle a connection from a single client, then kill the socket."""
        client_socket, address = self._socket.accept()
        if self._message_to_recv:
            self.message_received = client_socket.recv(len(self._message_to_recv))
        if self._message_to_send:
            client_socket.sendall(self._message_to_send)
        if self._die_after:
            self.kill_socket()


def _assert_raises_streamerror(func, *args):
    """Ensure that `func` called with `args` raises a `StreamError`.

    """
    with assert_raises(stream.StreamError) as cm:
        func(*args)
    stream_error = cm.exception
    return stream_error


def _assert_wraps_socket_error(stream_error):
    """Ensure that a `StreamError` wraps an underlying `socket.error`."""
    assert isinstance(
        stream_error.original_error, socket.error
    ), stream_error.original_error


class TestTcpStream:
    @staticmethod
    def _connect(server):
        """Create a TCP stream connected to a dummy server."""
        return stream.TCPStream(server.address, server.port)

    def test___init__(self):
        # Have the server hang on `recv`
        server = _DummyServer(message_to_recv=b"1")
        self._connect(server)

    def test_close(self):
        server = _DummyServer(message_to_recv=b"1")
        client = self._connect(server)
        client.close()
        assert_raises(stream.StreamError, client.recv, 1)

    def test_close_while_receiving(self):
        # Have the server take half the message, then hang.
        server = _DummyServer(message_to_recv=b"123", die_after=False)
        client = self._connect(server)

        def _failed_recv():
            assert_raises(stream.StreamError, client.recv, len(b"123456"))

        recv_thread = threading.Thread(target=_failed_recv)
        recv_thread.start()
        # Give it time to receive the first half. Theoretically this could
        # race, but not likely.
        time.sleep(0.1)
        # This should interrupt `recv` on the other thread.
        client.close()
        recv_thread.join(timeout=0.1)
        if recv_thread.isAlive():
            raise RuntimeError("Recv thread wasn't interrupted.")

    # TODO: Haven't explicitly tested a close while sending - more difficult
    #   than testing recv because it requires a server that hangs at the
    #   network level.

    def test___init___bad_target(self):
        # Try and connect to nonexistant server
        assert_raises(
            socket.error,
            stream.TCPStream,
            "adlgdshgeoiwuroeiwarlkdgjldsfh",  # Nonsense address
            32147,  # Random port, doesn't matter
        )

    def test_send(self):
        # Note this only tests that small messages work.

        # TODO: Possibly test sending long messages? Would need to upgrade the
        #   dummy server to recv them. Probably not worth it, will be covered
        #   by integration tests anyway.
        message_to_send = b"12345"
        server = _DummyServer(message_to_recv=message_to_send)
        client = self._connect(server)
        client.send(message_to_send)
        server.wait_until_terminate()
        eq_(message_to_send, server.message_received)

    def test_send_after_disconnect(self):
        # Have the server receive more than we're actually sending so it stays
        # alive.
        server = _DummyServer(message_to_recv=b"123456")
        client = self._connect(server)
        client.send(b"123")
        client.close()
        _assert_wraps_socket_error(_assert_raises_streamerror(client.send, b"456"))

    def test_recv(self):
        # We want to send a long message to check the chunking in `recv`.
        message = b"1" * (10 ^ 6)
        server = _DummyServer(message_to_send=message)
        client = self._connect(server)
        message_received = client.recv(len(message))
        assert (
            message_received == message
        ), "Messages didn't match. Target length: {}, Received length: {}".format(
            message_received.length, message.length
        )

    def test__recv_with_buffered_send(self):
        # What if the server is trying to send us more than we're willing to
        # receive? Do we only get what we want?
        long_message = b"1" * (10 ^ 4)
        server = _DummyServer(message_to_send=long_message)
        client = self._connect(server)
        desired_amount = 8
        message_received = client.recv(desired_amount)
        eq_(len(message_received), 8)

    def test_recv_interrupted(self):
        # Have the server send half the message, then die.
        server = _DummyServer(message_to_send=b"123")
        client = self._connect(server)
        # In this case there's no underlying error, but that isn't a guaranteed
        # property.
        _assert_raises_streamerror(client.recv, len(b"123456"))

    def test_recv_after_close(self):
        message = b"123"
        server = _DummyServer(message_to_send=message)
        client = self._connect(server)
        client.close()
        _assert_wraps_socket_error(
            _assert_raises_streamerror(client.recv, len(message))
        )

    def test_close_when_not_open(self):
        simple_message = b"1"
        server = _DummyServer(message_to_recv=simple_message)
        client = self._connect(server)
        client.send(simple_message)
        server.wait_until_terminate()
        # Already disconnected now - shouldn't raise an error.
        client.close()
        client.close()

    def test__recv_chunk(self):
        target_message = b"12345"
        server = _DummyServer(message_to_send=target_message)
        client = self._connect(server)
        message_received = client._recv_chunk(len(target_message))
        eq_(message_received, target_message)

    def test__recv_chunk_interrupted(self):
        # Have the server send half the message, then die.
        half_the_message = b"123"
        server = _DummyServer(message_to_send=half_the_message)
        client = self._connect(server)
        # Correct behaviour is for recv_chunk to receive as much as it can. It
        # only errors out if it gets nothing.
        chunk = client._recv_chunk(len(b"123456"))
        eq_(chunk, half_the_message)

    def test__recv_chunk_after_disconnect(self):
        server = _DummyServer(message_to_send=b"1234")
        client = self._connect(server)
        client._recv_chunk(len(b"123"))
        server.kill_socket()
        # There's something in the buffer - so the first attempt will receive
        # that.
        chunk = client._recv_chunk(len(b"456"))
        eq_(chunk, b"4")
        # The next attempt operates on a clear buffer. Now it will detect the
        # disconnect. Note no underlying error.
        _assert_raises_streamerror(client._recv_chunk, len(b"56"))

    def test__recv_chunk_after_close(self):
        server = _DummyServer(message_to_send=b"123456")
        client = self._connect(server)
        client._recv_chunk(len(b"123"))
        client.close()
        _assert_wraps_socket_error(
            _assert_raises_streamerror(client._recv_chunk, len(b"456"))
        )
