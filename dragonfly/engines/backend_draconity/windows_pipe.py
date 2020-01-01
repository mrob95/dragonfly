import threading
import win32pipe, win32file, pywintypes, win32event, winerror, win32api

from dragonfly.engines.backend_draconity.stream import StreamBase, StreamError


class PipeError(StreamError):
    """"Raised when there's an error calling the native windows pipe message."""

    def __init__(self, message, error_code):
        self.error_code = error_code
        full_message = message + " rc: {error_code}"
        super(PipeError, self).__init__(full_message)


class PipeStream(StreamBase):
    """Persistent stream for a pipe (with overlapped I/O)."""

    # TODO: Unit tests for pipe.

    def __init__(self, path):
        self._send_lock = threading.Lock()
        self._recv_lock = threading.Lock()
        self._handle = self._connect(path)

    @staticmethod
    def _connect(path):
        handle = win32file.CreateFile(
            path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            win32file.FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle == win32file.INVALID_HANDLE_VALUE:
            error_code = win32api.GetLastError()
            raise PipeError("Error connecting to pipe.", error_code)

        set_state_rc = win32pipe.SetNamedPipeHandleState(
            handle, win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT, None, None
        )
        # `SetNamedPipeHandleState` returns 0 on error
        if set_state_rc == 0:
            error_code = win32api.GetLastError()
            win32file.CloseFile(handle)
            raise PipeError("Error setting pipe state.", error_code)
        return handle

    def __del__(self):
        try:
            # Don't want the handle to dangle.
            self.close()
        except Exception:
            pass

    def close(self):
        # TODO: Is this the right way to close it?
        win32file.CloseHandle(self._handle)

    def send(self, data):
        with self._send_lock:
            overlapped = self._make_overlapped()
            try:
                write_rc, sent_data = win32file.WriteFile(
                    self._handle, data, overlapped
                )
                return self._get_result(
                    write_rc, self._handle, data, overlapped
                )
            except Exception:
                win32file.CloseHandle(self._handle)
                raise

    def recv(self, size):
        with self._recv_lock:
            overlapped = self._make_overlapped()
            try:
                read_rc, data = win32file.ReadFile(self._handle, size, overlapped)
                return self._get_result(
                    read_rc, self._handle, data, overlapped
                )
            except Exception:
                win32file.CloseHandle(self._handle)
                raise

    @staticmethod
    def _make_overlapped():
        overlapped = win32file.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, 0, 0, None)
        return overlapped

    @classmethod
    def _get_result(cls, rc, pipe_handle, data, overlapped):
        """Wait for the result of a Read or Write on the pipe."""
        # If `write_rc` is True, that means the operation has already
        # completed. Otherwise we have to get the last error.
        last_error = win32api.GetLastError()
        operation_ok = rc or last_error == winerror.ERROR_IO_PENDING or last_error == 0
        if operation_ok:
            bytes_transferred = cls._overlapped_get(pipe_handle, data, overlapped)

            if bytes_transferred < len(data):
                raise PipeError(
                    "Pipe disconnect while communicating.", win32api.GetLastError()
                )
            return data[:bytes_transferred]
        else:
            raise PipeError("Failed to communicate with pipe.", last_error)

    @staticmethod
    def _overlapped_get(pipe_handle, data, overlapped):
        wait_rc = win32event.WaitForSingleObject(overlapped.hEvent, win32event.INFINITE)
        success = wait_rc == win32event.WAIT_OBJECT_0
        if success:
            return win32file.GetOverlappedResult(pipe_handle, overlapped, 1)
        elif wait_rc == win32event.WAIT_TIMEOUT:
            raise PipeError("Operation timed out.", wait_rc)
        elif wait_rc == win32event.WAIT_ABANDONED:
            # Shouldn't ever get here.
            raise PipeError("Timeout abandoned.", wait_rc)
        else:
            # Should be: wait_rc == win32event.WAIT_FAILED
            raise PipeError("Failed to wait for pipe.", win32api.GetLastError())
