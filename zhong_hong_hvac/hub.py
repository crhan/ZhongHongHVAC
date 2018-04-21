"""Library to handle connection with ZhongHong Gateway."""
import logging
import socket
from collections import defaultdict, deque
from threading import Thread
from typing import Callable, DefaultDict, Deque

from . import helper, protocol

logger = logging.getLogger(__name__)

SOCKET_BUFSIZE = 1024


class ZhongHongGateway:
    def __init__(self, ip_addr: str, port: int, gw_addr: int):
        self.gw_addr = gw_addr
        self.ip_addr = ip_addr
        self.port = port
        self.sock = self.__get_socket()
        self.ac_callbacks = defaultdict(
            deque)  # type DefaultDict[protocol.AcAddr, Deque[Callable]]

        self._listening = False
        self._threads = []

    def __get_socket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.ip_addr, self.port))
        return s

    def _reopen_socket(self):
        if self.sock:
            self.sock.close()
            self.sock = None
        self.sock = self.__get_socket()
        return self.sock

    def add_status_callback(self, ac_addr: protocol.AcAddr,
                            func: Callable) -> None:
        self.ac_callbacks[ac_addr].append(func)

    def query_status(self, ac_addr: protocol.AcAddr) -> bool:
        message = protocol.AcData()
        message.header = protocol.Header(self.gw_addr,
                                         protocol.FuncCode.STATUS.value,
                                         protocol.CtlStatus.ONE.value, 1)
        message.add(ac_addr)
        return self.send(message)

    def send(self, ac_data: protocol.AcData) -> None:
        try:
            self.sock.settimeout(10.0)
            logger.debug("send >> %s", ac_data.encode())
            self.sock.send(ac_data.encode())

        except socket.timeout:
            logger.error("Connot connect to gateway %s:%s", self.ip_addr,
                         self.port)
            return None

    def _validate_data(self, data):
        if data is None:
            logger.error('No data in response from hub %s', data)
            return False

        return True

    def _listen_to_msg(self):
        while self._listening:
            try:
                data = self.sock.recv(SOCKET_BUFSIZE)
            except ConnectionResetError:
                self._reopen_socket()

            if not data:
                continue

            for ac_data in helper.get_ac_data(data):
                logger.debug("get ac_data << %s", ac_data)

                for payload in ac_data:
                    if isinstance(payload, protocol.AcStatus):
                        for func in self.ac_callbacks[payload.ac_addr]:
                            func(payload)

    def listen(self):
        """Start listening."""
        self._listening = True
        thread = Thread(target=self._listen_to_msg, args=())
        self._threads.append(thread)
        thread.daemon = True
        thread.start()

    def stop_listen(self):
        self._listening = False
        if self.sock:
            logger.info('Closing socket.')
            self.sock.close()
            self.sock = None

        for thread in self._threads:
            thread.join()
