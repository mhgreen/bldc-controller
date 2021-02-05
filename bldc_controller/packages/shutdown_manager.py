import signal

import trio
from loguru import logger


class ShutdownManager:
    # shutdown code is from https://github.com/python-trio/trio/blob/master/notes-to-self/graceful-shutdown-idea.py

    def __init__(self, on_shutdown=None):
        self._on_shutdown_list = on_shutdown
        self._shutting_down = False
        if self._on_shutdown_list is None:
            self._on_shutdown_list = []
        self._cancel_scopes = set()
        # limit the number of events that will be notified of a shutdown
        self._max_notify_list_length = 30

    @property
    def shutting_down(self):
        return self._shutting_down

    @property
    def on_shutdown(self):
        return self._on_shutdown_list

    @on_shutdown.setter
    def on_shutdown(self, new_shutdown_event):
        if len(self._on_shutdown_list) <= self._max_notify_list_length:
            self._on_shutdown_list.append(new_shutdown_event)
        else:
            raise RuntimeError(
                f'Adding to on_notify_list failed, exceeds maximum number of entries which is currently {self._max_notify_list_length}')

    def _start_shutdown(self):
        """Begin the shutdown process.
        """
        logger.info(f'starting shutdown')
        self._shutting_down = True
        # Traverse the list of events that should be notified of a shutdown, setting each.
        if self._on_shutdown_list:
            for notify_item in self._on_shutdown_list:
                notify_item.set()
        self._on_shutdown_list = []
        for cancel_scope in self._cancel_scopes:
            cancel_scope.cancel()

    def cancel_on_shutdown(self):
        """Cancel scopes upon shutdown

        Usage::

            with self.cancel_on_shutdown():
                some code that should run until shutdown, likely an infinite loop
        """
        cancel_scope = trio.CancelScope()
        self._cancel_scopes.add(cancel_scope)
        if self._shutting_down:
            cancel_scope.cancel()
        return cancel_scope

    async def _listen_for_shutdown_signals(self):
        """Listen for signals, potentially uniquely handling different signals, and initiating shutdown of event loops.
        """
        logger.debug(f'listening for shutdown signals')
        with trio.open_signal_receiver(signal.SIGINT, signal.SIGTERM, signal.SIGHUP) as signals:
            async for signal_number in signals:
                logger.debug(f'---- a shutdown signal has been received ----')
                if signal_number == signal.SIGINT:
                    logger.info(f'received SIGINT, running start_shutdown')
                elif signal_number == signal.SIGTERM:
                    logger.info(f'received SIGTERM, running start_shutdown')
                elif signal_number == signal.SIGHUP:
                    logger.info(f'received SIGHUP, running start_shutdown')
                self._start_shutdown()
                break

    def __enter__(self):
        return self

    def __exit__(self):
        self._start_shutdown()
