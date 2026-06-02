"""Tests for ZhongHong gateway behavior."""

import os

import pytest

from zhong_hong_hvac import protocol
from zhong_hong_hvac.hub import ZhongHongGateway
from zhong_hong_hvac.hvac import HVAC

LOCAL_PORT = 9999
LOCAL_HOST = "192.168.15.19"


@pytest.mark.skipif(
    not os.getenv("ZHONG_HONG_HVAC_RUN_LOCAL_TESTS"),
    reason="requires a local ZhongHong gateway",
)
def test_local_connection():
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    devices = [
        HVAC(gw=gw, addr_out=addr_out, addr_in=addr_in)
        for (addr_out, addr_in) in gw.discovery_ac()
    ]
    gw.query_all_status()
    data = gw._get_data()
    if len(data) < 25:
        data = gw._get_data()

    assert devices
    first_device = devices[0]
    assert first_device.switch_status is None

    gw._listen_to_msg(data)
    assert first_device.switch_status is not None


class FakeSocket:
    def __init__(self, recv_values=None, send_errors=None):
        self.recv_values = list(recv_values or [])
        self.send_errors = list(send_errors or [])
        self.closed = False
        self.sent = []
        self.timeouts = []

    def close(self):
        self.closed = True

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def recv(self, _bufsize):
        value = self.recv_values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def sendall(self, data):
        if self.send_errors:
            raise self.send_errors.pop(0)
        self.sent.append(data)


def _status_request():
    request_data = protocol.AcData()
    request_data.header = protocol.Header(
        1,
        protocol.FuncCode.STATUS,
        protocol.CtlStatus.ONE,
        1,
    )
    request_data.add(protocol.AcAddr(1, 1))
    return request_data


def test_get_data_reconnects_when_gateway_closes_connection(monkeypatch):
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    old_socket = FakeSocket(recv_values=[b""])
    new_socket = FakeSocket()
    sockets = iter([new_socket])
    monkeypatch.setattr(gw, "_ZhongHongGateway__get_socket", lambda: next(sockets))
    gw._connect_retry_delay = 0
    gw.sock = old_socket

    assert gw._get_data() is None
    assert old_socket.closed
    assert gw.sock is new_socket


def test_send_reconnects_and_retries_after_broken_pipe(monkeypatch):
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    old_socket = FakeSocket(send_errors=[BrokenPipeError()])
    new_socket = FakeSocket()
    sockets = iter([new_socket])
    monkeypatch.setattr(gw, "_ZhongHongGateway__get_socket", lambda: next(sockets))
    gw._connect_retry_delay = 0
    gw.sock = old_socket

    request = _status_request()

    assert gw.send(request) is True
    assert old_socket.closed
    assert new_socket.sent == [request.encode()]
