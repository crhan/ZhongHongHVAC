"""Library to handle connection with ZhongHong Gateway."""
import logging
import socket
import time
from collections import defaultdict
from sys import platform
from threading import Thread
from typing import Callable, DefaultDict, List

import attr

from . import helper, protocol

logger = logging.getLogger(__name__)

SOCKET_BUFSIZE = 1024


class ZhongHongGateway:
    def __init__(self, ip_addr: str, port: int, gw_addr: int):
        self.gw_addr = gw_addr
        self.ip_addr = ip_addr
        self.port = port
        self.sock = None
        self.ac_callbacks = defaultdict(
            list)  # type DefaultDict[protocol.AcAddr, List[Callable]]
        self.devices = {}
        self._listening = False
        self._threads = []
        self.max_retry = 5

    def __get_socket(self) -> socket.socket:
        logger.debug("Opening socket to (%s, %s)", self.ip_addr, self.port)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if platform in ('linux', 'linux2'):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 1)  # pylint: disable=E1101
        if platform in ('darwin', 'linux', 'linux2'):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        s.connect((self.ip_addr, self.port))
        return s

    def open_socket(self):
        if self.sock:
            self.sock.close()
            self.sock = None
            time.sleep(1)

        self.sock = self.__get_socket()
        return self.sock

    def add_status_callback(self, ac_addr: protocol.AcAddr,
                            func: Callable) -> None:
        logger.debug("%s adding status callback", ac_addr)
        self.ac_callbacks[ac_addr].append(func)

    def add_device(self, device) -> None:
        logger.debug("device %s add to hub %s", device.ac_addr, self.gw_addr)
        self.devices[attr.astuple(device.ac_addr)] = device

    def get_device(self, addr: protocol.AcAddr):
        return self.devices.get(attr.astuple(addr))

    def query_status(self, ac_addr: protocol.AcAddr) -> bool:
        message = protocol.AcData()
        message.header = protocol.Header(self.gw_addr,
                                         protocol.FuncCode.STATUS.value,
                                         protocol.CtlStatus.ONE.value, 1)
        message.add(ac_addr)
        return self.send(message)

    def send(self, ac_data: protocol.AcData) -> None:
        def _send(retry_count):
            try:
                self.sock.settimeout(10.0)
                logger.debug("send >> %s", ac_data.hex())
                self.sock.send(ac_data.encode())
                self.sock.settimeout(None)

            except socket.timeout:
                logger.error("Connot connect to gateway %s:%s", self.ip_addr,
                             self.port)
                return

            except OSError as e:
                if e.errno == 32:  # Broken pipe
                    logger.error("OSError 32 raise, Broken pipe", exc_info=e)
                if retry_count < self.max_retry:
                    retry_count += 1
                    self.open_socket()
                    _send(retry_count)

        _send(0)

    def _validate_data(self, data):
        if data is None:
            logger.error('No data in response from hub %s', data)
            return False

        return True

    def _get_data(self):
        if self.sock is None:
            self.open_socket()

        try:
            return self.sock.recv(SOCKET_BUFSIZE)

        except ConnectionResetError:
            logger.debug("Connection reset by peer")
            self.open_socket()

        except socket.timeout as e:
            logger.error("timeout error", exc_info=e)

        except OSError as e:
            if e.errno == 9:  # when socket close, errorno 9 will raise
                logger.debug("OSError 9 raise, socket is closed")

            else:
                logger.error("unknown error when recv", exc_info=e)

        except Exception as e:
            logger.error("unknown error when recv", exc_info=e)

        return None

    def _listen_to_msg(self):
        while self._listening:
            data = self._get_data()

            if not data:
                continue

            logger.debug("recv data << %s", protocol.bytes_debug_str(data))

            for ac_data in helper.get_ac_data(data):
                logger.debug("get ac_data << %s", ac_data)

                if ac_data.func_code == protocol.FuncCode.STATUS:
                    for payload in ac_data:
                        if not isinstance(payload, protocol.AcStatus):
                            continue

                        logger.debug("get payload << %s", payload)
                        for func in self.ac_callbacks[payload.ac_addr]:
                            func(payload)

                elif ac_data.func_code in (protocol.FuncCode.CTL_POWER,
                                           protocol.FuncCode.CTL_TEMPERATURE,
                                           protocol.FuncCode.CTL_OPERATION,
                                           protocol.FuncCode.CTL_FAN_MODE):
                    header = ac_data.header
                    for payload in ac_data:
                        device = self.get_device(payload)
                        device.set_attr(header.func_code, header.ctl_code)

    def start_listen(self):
        """Start listening."""
        if self._listening:
            logger.info("Hub %s is listening", self.gw_addr)
            return True

        if self.sock is None:
            self.open_socket()

        self._listening = True
        thread = Thread(target=self._listen_to_msg, args=())
        self._threads.append(thread)
        thread.daemon = True
        thread.start()
        logger.info("Start message listen thread %s", thread.ident)
        return True

    def stop_listen(self):
        logger.debug("Stopping hub %s", self.gw_addr)
        self._listening = False
        if self.sock:
            logger.info('Closing socket.')
            self.sock.close()
            self.sock = None

        for thread in self._threads:
            thread.join()

    def discovery_ac(self):
        assert not self._listening

        if self.sock is None:
            self.open_socket()

        ret = []
        request_data = protocol.AcData()
        request_data.header = protocol.Header(
            self.gw_addr, protocol.FuncCode.STATUS, protocol.CtlStatus.ONLINE,
            protocol.CtlStatus.ALL)
        request_data.add(protocol.AcAddr(0xff, 0xff))

        discovered = False
        count_down = 10
        while not discovered and count_down >= 0:
            count_down -= 1
            logger.debug("send discovery request: %s", request_data.hex())
            self.send(request_data)
            data = self._get_data()

            if data is None:
                logger.error("No response from gateway")

            for ac_data in helper.get_ac_data(data):
                if ac_data.header != request_data.header:
                    logger.debug("header not match: %s != %s",
                                 request_data.header, ac_data.header)
                    continue

                for ac_online in ac_data:
                    assert isinstance(ac_online, protocol.AcOnline)
                    ret.append((ac_online.addr_out, ac_online.addr_in))

                discovered = True

        return ret

    def query_all_status(self) -> None:
        request_data = protocol.AcData()
        request_data.header = protocol.Header(
            self.gw_addr, protocol.FuncCode.STATUS, protocol.CtlStatus.ALL,
            protocol.CtlStatus.ALL)
        request_data.add(
            protocol.AcAddr(protocol.CtlStatus.ALL, protocol.CtlStatus.ALL))

        self.send(request_data)
