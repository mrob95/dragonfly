import struct
import itertools
import threading
import traceback

import bson

from dragonfly.engines.backend_draconity import stream


_DRACONITY_HEADER_STRUCT = struct.Struct(">II")


class NotConnectedError(RuntimeError):
    """To be raised when the client is not connected."""


class DraconityClient(object):
    """Client that allows messages to be passed to/from Draconity.

    Define a set of callbacks to handle messages, errors and disconnections.
    Then, connect over an open byte stream - whether this is TCP or Pipe-based
    doesn't matter (see `stream.StreamBase` for details). The client will
    continually receive and handle messages in a background thread. You can
    send messages yourself with `send` - they will be assigned a TID and sent
    over the stream.

    """

    def __init__(self, on_message, on_error=None, on_disconnect=None):
        """Create a new message receiver.

        :param on_message: a callback that will be called when a full message
          has been received. It should take two parameters - a tid, and a
          message.
        :type on_message: Function(int, dict)

        :param on_error: Optional. A callback that will be called when an error
          causes the socket to disconnect. It should take one parameter - the
          original error associated with the interruption. Note that the
          `on_disconnect` callback will also be called on an erroneous
          disconnect - after this one. Default None.
        :type on_error: Function(Exception)

        :param on_disconnect: Optional. A callback that will be called when the
          stream is disconnected. It should take no arguments.
        :type on_disconnect: Function()

        """
        self._on_message = on_message
        self._on_error = on_error
        self._on_disconnect = on_disconnect

        self._stream = None
        self._receiver = None
        # TID = Transaction ID. Unique across individual connections.
        #
        # Messages from clients may not have a TID of 0, because 0 is reserved
        # for standalone messages from Draconity - so it wouldn't be able to
        # reply. So we start at 1.
        #
        # Note the counter for this client persists across multiple
        # connections.
        self._tid_counter = itertools.count(1)
        # The stream might error out when we manually disconnect. This flag
        # tells the `_receiver` it was intentional.
        self._deliberately_closed = None

    def connect(self, stream):
        """Connect this client to Draconity over an already established stream.

        Messages can be sent once the client is connected. The client will
        begin receiving messages from the stream (and invoking callbacks) on a
        background thread immediately.

        :param stream: the stream over which messages should be received.
        :type stream: stream.StreamBase
        :returns: nothing
        :raises RuntimeError: if already connected (and the connection is
        active).

        """
        if self.connected:
            raise RuntimeError("Already connected. Please close the connection first.")

        self._deliberately_closed = False

        self._stream = stream
        self._receiver = threading.Thread(target=self._recv_messages)
        self._receiver.start()

    def _recv_messages(self):
        """Pump messages from Draconity (until disconnect).

        Each message is passed to the `_on_message` callback. If an error
        interrupts the stream, it will be passed to `_on_error`. The
        `_on_close` callback is called when the stream is closed.

        Note all these callbacks are called in this thread, the message pumping
        thread. They should not be resource intensive.

        """
        try:
            while True:
                tid, message = self._pump_one_message()
                self._handle_message(tid, message)
        except Exception as error:
            if not self._deliberately_closed:
                self._handle_error(error)
        # Absolutely ensure the stream is closed.
        self._safely_close_stream()
        self._handle_disconnect()

    def _pump_one_message(self):
        """Receive a single message from Draconity.

        Note this method assumes messages have the right structure. An
        unexpected message structure will cause undefined behavior (it will
        probably either error out or hang).

        """
        # TODO: Handle malformed structure gracefully?
        tid, size = self._receive_header()
        message_body = self._receive_body(size)
        return tid, message_body

    def _receive_header(self):
        """Receive the header of a message from the stream (and unpack it).

        :returns: the TID of the message, and its size, in bytes.
        :rtype: (int, int)

        """
        header = self._stream.recv(_DRACONITY_HEADER_STRUCT.size)
        tid, size = _DRACONITY_HEADER_STRUCT.unpack(header)
        return tid, size

    def _receive_body(self, size):
        """Receive the body of a message from the stream.

        :param size: size of the message, in bytes.
        :returns dict: the decoded message.

        """
        bson_body = self._stream.recv(size)
        return bson.decode_all(bson_body)[0]

    def _communicate_exception(self, message):
        """Communicate an exception to the user."""
        # TODO: Figure out the correct way to communicate exceptions to the
        #   user.
        traceback.print_exc(limit=40)

    def _handle_error(self, error):
        if callable(self._on_error):
            try:
                self._on_error(error)
            except Exception as e:
                self._communicate_exception(
                    "While handling a Draconity Stream error, another error occurred."
                )

    def _handle_disconnect(self):
        if callable(self._on_disconnect):
            try:
                self._on_disconnect()
            except Exception as e:
                self._communicate_exception(
                    "An error occured handling the Draconity disconnect."
                )

    def _handle_message(self, tid, message):
        try:
            self._on_message(tid, message)
        except Exception as e:
            self._communicate_exception("An error occured handling the error.")

    def _safely_close_stream(self):
        """Close the stream, if it's open. Ignore errors.

        (Note that closing the stream may still cause errors in other threads.)

        """
        try:
            self._stream.close()
        except Exception:
            pass
        self._stream = None

    def close(self):
        """Close the current connection.

        (Does nothing if client is not connected.)

        """
        self._deliberately_closed = True
        self._safely_close_stream()
        self._receiver.join()

    @property
    def connected(self):
        """Is this client currently connected to Draconity?"""
        return self._stream and self._receiver and self._receiver.isAlive()

    def send(self, message):
        """Send a message to Draconity.

        :param dict message: the message to send
        :returns int: the tid assigned to the message
        :raises stream.StreamError: when a problem occurs sending the message.

        """
        tid, prepped_message = self._prep_message(message)
        try:
            # `_stream` could be deleted in a race so we guard this with a try.
            self._stream.send(prepped_message)
        except AttributeError as e:
            if self._stream is None:
                raise stream.StreamError("Client not connected.")
            else:
                raise
        return tid

    def _prep_message(self, message):
        """Encode a message to be sent over the socket to Draconity.

        :param dict message: the message to send.
        :returns: two values, the tid assigned to the message and the full
          encoded message.
        :rtype: (int, bytes)

        """
        bson_message = bson.BSON.encode(message)
        tid = next(self._tid_counter)
        header = _DRACONITY_HEADER_STRUCT.pack(tid, len(bson_message))
        full_message = header + bson_message
        return tid, full_message
