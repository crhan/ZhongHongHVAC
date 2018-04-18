"""Library to handle connection with ZhongHong Gateway."""
import logging
import socket
from collections import defaultdict, deque
from typing import Callable, DefaultDict, Deque

from . import protocol

logger = logging.getLogger(__name__)


class ZhongHongGateway:
    def __init__(self, ip_addr: str, port: int, gw_addr: int):
        self.gw_addr = gw_addr
        self.ip_addr = ip_addr
        self.port = port
        self.sock = self.__get_socket()
        self.callbacks = defaultdict(
            deque)  # type: DefaultDict[protocol.Header, deque]
        self.ac_callbacks = defaultdict(
            deque)  # type DefaultDict[protocol.AcAddr, Deque[Callable]]

    def __get_socket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.ip_addr, self.port))
        return s

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

    def send(self, ac_data: protocol.AcData) -> bool:
        resp = self._send(ac_data)
        logger.debug("send << %s", resp)
        return self.push_data(resp)

    def _send(self, ac_data: protocol.AcData) -> protocol.AcData:
        try:
            self.sock.settimeout(10.0)
            logger.debug("send >> %s", ac_data.encode())
            self.sock.send(ac_data.encode())
            data = self.sock.recv(1024)

        except socket.timeout:
            logger.error("Connot connect to gateway %s:%s", self.ip_addr,
                         self.port)
            return None

        if data is None:
            logger.error("No response from gateway")
        try:
            resp = protocol.parse_data(data)
            logger.debug("send << %s", str(resp))
        except protocol.ChecksumError:
            logger.error("checksum error")
            return None

        if resp.header != ac_data.header:
            logger.error("No matching response. Expect %s, but got %s",
                         ac_data.header, resp.header)
            return None
        return resp

    def push_data(self, data: protocol.AcData) -> bool:
        if not self._validate_data(data):
            return False

        for _data in data.payload:
            if isinstance(_data, protocol.AcStatus):
                for func in self.ac_callbacks[_data.ac_addr]:
                    func(_data)

        return True

    def _validate_data(self, data):
        if data is None:
            logger.error('No data in response from hub %s', data)
            return False

        return True
