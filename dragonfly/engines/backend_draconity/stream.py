"""Base file for dealing with Draconity network streams.

Includes the abstract interface and a TCP implementation. All streams should
implement the abstract interface.

Draconity can communicate over TCP and named pipes, but the named pipe
implementation will depend on the platform. The purpose of the `Stream`
construct is to abstract away the underlying communication mechanism, and just
treat it as "put bytes in, get bytes back". The particular stream can then be
injected into a higher-level client.

"""

import socket


# TODO: There's some Python 2/3 interop in this module. Run tests in Python 3
#   to verify it works.


# `reduce` has to be imported from `functools` on some Python versions.
if not ("reduce" in globals() and callable(reduce)):
    from functools import reduce


class StreamError(RuntimeError):
    """Raised when a connection unexpectedly fails or is interrupted.

    The failure might have raised an exception - this can be attached as the
    `original_error`.

    """

    def __init__(self, message, original_error=None):
        self.original_error = original_error
        full_message = ' Original Error: "{}"'.format(original_error)
        super(StreamError, self).__init__(full_message)


class Stream(object):
    """Interface for some kind of network stream.

    Bytes can be sent or received over the stream.

    This class is designed to abstract the specific transport mechanism used to
    connect to Draconity - you can connect via socket, pipe, etc. and it will
    behave similarly.

    Implementations must connect on creation - they may not be created in a
    disconnected state (although they may disconnect later).

    """

    # TODO: Possibly close on __del__?

    def close(self):
        """Formally close the stream.

        This will interrupt any `send` or `recv` methods currently in progress.

        """
        raise NotImplementedError("Interface not implemented.")

    def send(self, data):
        """Send data over the stream.

        :param bytes data: the data to send
        :raises StreamError: if the Stream is disconnected (or encounters
          an issue) while sending.

        """
        raise NotImplementedError("Interface not implemented.")

    def recv(self, size):
        """Receive data from the stream.

        :param int size: the amount of data to receive (in bytes).
        :raises StreamError: if the Stream is disconnected (or encounters
          an issue) while receiving.

        """
        raise NotImplementedError("Interface not implemented.")


class TCPStream(Stream):

    def __init__(self, host, port):
        """Create and establish a TCP stream.

        Note that unlike `send` and `recv`, errors are not wrapped. Will raise
        socket errors directly if there are any issues connecting.

        """
        self._socket = socket.socket()
        return self._socket.connect((host, port))

    def close(self):
        """Deliberately close the socket.

        This may interrupt `send` or `recv` calls that are currently in
        progress, throwing an error.

        """
        # TODO: Wrap
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except socket.error:
            pass
        self._socket.close()

    def send(self, chunk):
        # TODO: Timeout?
        try:
            self._socket.sendall(chunk)
        except Exception as error:
            raise StreamError("Connection interrupted.", error)

    def recv(self, size):
        """Receive bytes from the server.

        :param int size: the number of bytes to receive.
        :raises StreamError: if there's an issue receiving the bytes or the
          connection is dropped, a StreamError will be raised. If an error was
          raised by the underlying socket's `recv` method, it will be attached.
        :returns str: the message received.

        """
        # Use list so we can append either bytes (py2) or string (py3).
        data = []
        received = 0
        while received < size:
            chunk = self._recv_chunk(max(size - received, 2048))
            data.append(chunk)
            received += len(chunk)
        # Reduce combines the data but also normalizes to a string.
        return reduce(lambda a, b: a + b, data)

    def _recv_chunk(self, size):
        """Receive one chunk of data from the server.

        This method may only receive part of the data. Call it repeatedly to
        get subsequent chunks.

        :param int size: size of the chunk, in bytes.
        :raises StreamError: see `recv` for details.
        :returns: as much of the chunk as could be received.

        """
        try:
            chunk = self._socket.recv(size)
        except Exception as error:
            raise StreamError("Error receiving bytes.", error)
        if not chunk:
            raise StreamError("Connection interrupted.")
        return chunk


# TODO: Windows pipe stream
# TODO: Mac pipe stream?
