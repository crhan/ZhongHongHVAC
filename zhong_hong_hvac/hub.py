"""Library to handle connection with ZhongHong Gateway."""

import logging
import socket
import time
from collections import defaultdict
from sys import platform
from threading import RLock, Thread
from typing import Callable, DefaultDict, List, Optional

import attr

from . import helper, protocol

logger = logging.getLogger(__name__)

SOCKET_BUFSIZE = 1024

# Connection robustness defaults. They can be tuned per instance before
# start_listen() is called.
DEFAULT_RECV_TIMEOUT = 30.0
DEFAULT_PROBE_INTERVAL = 60.0
DEFAULT_PROBE_RESPONSE_TIMEOUT = 10.0
DEFAULT_MAX_PROBE_FAILURES = 3
DEFAULT_STALE_TIMEOUT = 300.0
CONNECT_FAILURE_LOG_INTERVAL = 30.0


class ZhongHongGateway:
    def __init__(self, ip_addr: str, port: int, gw_addr: int):
        self.gw_addr = gw_addr
        self.ip_addr = ip_addr
        self.port = port
        self.sock = None
        self.ac_callbacks = defaultdict(
            list
        )  # type DefaultDict[protocol.AcAddr, List[Callable]]
        self.devices = {}
        self._listening = False
        self._threads = []
        self.max_retry = 5
        self._connect_retry_delay = 1
        self._socket_lock = RLock()

        # Connection robustness state
        self._recv_timeout = DEFAULT_RECV_TIMEOUT
        self._probe_interval = DEFAULT_PROBE_INTERVAL
        self._probe_response_timeout = DEFAULT_PROBE_RESPONSE_TIMEOUT
        self._max_probe_failures = DEFAULT_MAX_PROBE_FAILURES
        self._stale_timeout = DEFAULT_STALE_TIMEOUT
        self._listener_alive = False
        self._last_seen = 0.0
        self._last_seen_wall = None
        self._probe_pending = False
        self._probe_sent_at = 0.0
        self._probe_failures = 0
        self._last_connect_failure_log = 0.0

    def __get_socket(self) -> socket.socket:
        logger.debug("Opening socket to (%s, %s)", self.ip_addr, self.port)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if platform in ("linux", "linux2"):
            s.setsockopt(
                socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 1
            )  # pylint: disable=E1101
        if platform in ("darwin", "linux", "linux2"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        s.settimeout(self._recv_timeout)
        s.connect((self.ip_addr, self.port))
        return s

    def open_socket(self):
        with self._socket_lock:
            if self.sock:
                self.sock.close()
                self.sock = None
                time.sleep(self._connect_retry_delay)

            self.sock = self.__get_socket()
            return self.sock

    def _ensure_socket(self):
        with self._socket_lock:
            if self.sock is None:
                return self.open_socket()
            return self.sock

    def _reconnect_socket(self, failed_sock=None):
        with self._socket_lock:
            if failed_sock is not None and self.sock is not failed_sock:
                return self.sock
            return self.open_socket()

    def _try_reconnect_socket(self, failed_sock=None):
        try:
            return self._reconnect_socket(failed_sock)
        except OSError as e:
            logger.error("Cannot reconnect to gateway", exc_info=e)
            time.sleep(self._connect_retry_delay)
            return None

    def add_status_callback(self, ac_addr: protocol.AcAddr, func: Callable) -> None:
        logger.debug("%s adding status callback", ac_addr)
        self.ac_callbacks[ac_addr].append(func)

    def add_device(self, device) -> None:
        logger.debug("device %s add to hub %s", device.ac_addr, self.gw_addr)
        self.devices[attr.astuple(device.ac_addr)] = device

    def get_device(self, addr: protocol.AcAddr):
        return self.devices.get(attr.astuple(addr))

    def query_status(self, ac_addr: protocol.AcAddr) -> bool:
        message = protocol.AcData()
        message.header = protocol.Header(
            self.gw_addr,
            protocol.FuncCode.STATUS.value,
            protocol.CtlStatus.ONE.value,
            1,
        )
        message.add(ac_addr)
        return self.send(message)

    def send(self, ac_data: protocol.AcData) -> bool:
        encoded_data = ac_data.encode()
        for retry_count in range(self.max_retry + 1):
            sock = None
            try:
                sock = self._ensure_socket()
                sock.settimeout(10.0)
                logger.debug("send >> %s", ac_data.hex())
                sock.sendall(encoded_data)
                return True

            except socket.timeout:
                logger.error("Cannot send to gateway %s:%s", self.ip_addr, self.port)

            except OSError as e:
                if e.errno == 32:  # Broken pipe
                    logger.error("OSError 32 raise, Broken pipe", exc_info=e)
                else:
                    logger.error("socket error when send", exc_info=e)

            finally:
                if sock is not None:
                    try:
                        sock.settimeout(self._recv_timeout)
                    except OSError:
                        pass

            if retry_count < self.max_retry:
                self._try_reconnect_socket(sock)

        return False

    def _validate_data(self, data):
        if data is None:
            logger.error("No data in response from hub %s", data)
            return False

        return True

    def _get_data(self):
        try:
            sock = self._ensure_socket()
        except OSError as e:
            now = time.monotonic()
            if now - self._last_connect_failure_log >= CONNECT_FAILURE_LOG_INTERVAL:
                logger.error(
                    "Cannot connect to gateway %s:%s",
                    self.ip_addr,
                    self.port,
                    exc_info=e,
                )
                self._last_connect_failure_log = now
            time.sleep(self._connect_retry_delay)
            return None

        try:
            data = sock.recv(SOCKET_BUFSIZE)
            if data == b"":
                logger.debug("Connection closed by gateway")
                self._try_reconnect_socket(sock)
                return None
            return data

        except ConnectionResetError:
            logger.debug("Connection reset by peer")
            self._try_reconnect_socket(sock)

        except socket.timeout:
            # No data within the timeout. This is not a failure by itself:
            # the listener uses it as a tick to run the health probe below.
            return None

        except OSError as e:
            if e.errno == 9:  # when socket close, errorno 9 will raise
                logger.debug("OSError 9 raise, socket is closed")
                return None

            logger.error("unknown error when recv", exc_info=e)
            self._try_reconnect_socket(sock)

        except Exception as e:
            logger.error("unknown error when recv", exc_info=e)
            self._try_reconnect_socket(sock)

        return None

    def _mark_seen(self):
        """Record that the gateway is alive and talking to us."""
        self._last_seen = time.monotonic()
        self._last_seen_wall = time.time()
        self._probe_pending = False
        self._probe_failures = 0

    def _note_probe_failure(self, reason: str):
        self._probe_failures += 1
        logger.warning(
            "Gateway health probe failed (%s): %d/%d",
            reason,
            self._probe_failures,
            self._max_probe_failures,
        )
        if self._probe_failures >= self._max_probe_failures:
            self._probe_failures = 0
            self._try_reconnect_socket()

    def _maybe_probe(self):
        now = time.monotonic()
        if self._last_seen <= 0 or now - self._last_seen < self._probe_interval:
            return

        if self._probe_pending:
            if now - self._probe_sent_at >= self._probe_response_timeout:
                self._probe_pending = False
                self._note_probe_failure("gateway did not respond to status probe")
            return

        if self.query_all_status():
            self._probe_pending = True
            self._probe_sent_at = now
            logger.debug("Status probe sent to gateway")
        else:
            self._note_probe_failure("status probe send failed")

    def _listener_iteration(self):
        """One pass of the listener loop. Runs on the listener thread."""
        data = self._get_data()
        if data:
            self._mark_seen()
            self._listen_to_msg(data)
        else:
            self._maybe_probe()

    def thread_main(self):
        """Listen for gateway pushes and drive the connection health probe."""
        self._listener_alive = True
        try:
            while self._listening:
                try:
                    self._listener_iteration()
                except Exception as e:
                    logger.error("Unexpected error in listener loop", exc_info=e)
                    time.sleep(self._connect_retry_delay)
        finally:
            self._listener_alive = False
            logger.debug("Listener thread exited for hub %s", self.gw_addr)

    def _listen_to_msg(self, data):
        logger.debug("recv data << %s", protocol.bytes_debug_str(data))

        try:
            for ac_data in helper.get_ac_data(data):
                try:
                    logger.debug("get ac_data << %s", ac_data)

                    if ac_data.func_code == protocol.FuncCode.STATUS:
                        for payload in ac_data:
                            if not isinstance(payload, protocol.AcStatus):
                                continue

                            logger.debug("get payload << %s", payload)
                            for func in self.ac_callbacks[payload.ac_addr]:
                                func(payload)

                    elif ac_data.func_code in (
                        protocol.FuncCode.CTL_POWER,
                        protocol.FuncCode.CTL_TEMPERATURE,
                        protocol.FuncCode.CTL_OPERATION,
                        protocol.FuncCode.CTL_FAN_MODE,
                    ):
                        header = ac_data.header
                        for payload in ac_data:
                            device = self.get_device(payload)
                            if device is not None:
                                device.set_attr(header.func_code, header.ctl_code)
                except Exception as e:
                    logger.error("Failed to handle message %s: %s", ac_data, e)
                    continue
        except Exception as e:
            logger.error("Failed to process received data: %s", e)

    def start_listen(self):
        """Start listening."""
        if self._listening:
            logger.info("Hub %s is listening", self.gw_addr)
            return True

        if self.sock is None:
            self.open_socket()

        self._listening = True
        thread = Thread(target=self.thread_main, args=())
        self._threads.append(thread)
        thread.daemon = True
        thread.start()
        logger.info("Start message listen thread %s", thread.ident)
        return True

    def stop_listen(self):
        logger.debug("Stopping hub %s", self.gw_addr)
        self._listening = False
        if self.sock:
            logger.info("Closing socket.")
            self.sock.close()
            self.sock = None

        for thread in self._threads:
            thread.join()

    @property
    def connected(self) -> bool:
        """Whether the gateway connection is alive and healthy."""
        if not self._listening or self.sock is None or not self._listener_alive:
            return False
        if self._last_seen <= 0:
            return True
        return time.monotonic() - self._last_seen <= self._stale_timeout

    @property
    def last_seen(self) -> float:
        """Monotonic timestamp of the last data received from the gateway."""
        return self._last_seen

    @property
    def last_seen_wall(self) -> Optional[float]:
        """Wall-clock timestamp of the last data received from the gateway."""
        return self._last_seen_wall

    def discovery_ac(self):
        assert not self._listening

        if self.sock is None:
            self.open_socket()

        ret = []
        request_data = protocol.AcData()
        request_data.header = protocol.Header(
            self.gw_addr,
            protocol.FuncCode.STATUS,
            protocol.CtlStatus.ONLINE,
            protocol.CtlStatus.ALL,
        )
        request_data.add(protocol.AcAddr(0xFF, 0xFF))

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
                    logger.debug(
                        "header not match: %s != %s",
                        request_data.header,
                        ac_data.header,
                    )
                    continue

                for ac_online in ac_data:
                    assert isinstance(ac_online, protocol.AcOnline)
                    ret.append((ac_online.addr_out, ac_online.addr_in))

                discovered = True

        return ret

    def query_all_status(self) -> bool:
        request_data = protocol.AcData()
        request_data.header = protocol.Header(
            self.gw_addr,
            protocol.FuncCode.STATUS,
            protocol.CtlStatus.ALL,
            protocol.CtlStatus.ALL,
        )
        request_data.add(
            protocol.AcAddr(protocol.CtlStatus.ALL, protocol.CtlStatus.ALL)
        )

        return self.send(request_data)
