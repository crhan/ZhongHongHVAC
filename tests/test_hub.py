"""Tests for ZhongHong gateway behavior."""

import os
import threading
import time

import pytest

from zhong_hong_hvac import helper, protocol
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


def _status_frame(addr_out, addr_in, switch, temp, mode, fan, room_temp, error):
    payload = bytes(
        [addr_out, addr_in, switch, temp, mode, fan, room_temp, error, 0, 0]
    )
    frame = (
        bytes([1, protocol.FuncCode.STATUS.value, protocol.CtlStatus.ONE.value, 1])
        + payload
    )
    checksum = sum(frame) % 256
    return frame + bytes([checksum])


def test_status_with_unknown_fan_mode_is_tolerated():
    # Fan speed 0x00 is not part of the protocol spec but has been observed in
    # the field right after an indoor unit is switched off.
    frame = _status_frame(1, 1, 1, 24, 1, 0, 25, 0)

    ac_data = list(helper.get_ac_data(frame))

    assert len(ac_data) == 1
    status = next(iter(ac_data[0]))
    assert status.current_fan_mode is None
    assert status.current_operation == protocol.StatusOperation.COOL


def test_status_with_unknown_operation_is_tolerated():
    frame = _status_frame(1, 1, 1, 24, 0, 4, 25, 0)

    ac_data = list(helper.get_ac_data(frame))

    status = next(iter(ac_data[0]))
    assert status.current_operation is None
    assert status.current_fan_mode == protocol.StatusFanMode.LOW


def test_get_ac_data_skips_single_bad_frame(monkeypatch):
    good = _status_frame(1, 1, 1, 24, 1, 4, 25, 0)
    calls = {"n": 0}
    original_parse = helper.parse_data

    def flaky_parse(data_frame):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("boom")
        return original_parse(data_frame)

    monkeypatch.setattr(helper, "parse_data", flaky_parse)

    messages = list(helper.get_ac_data(good + good))

    assert calls["n"] == 2
    assert len(messages) == 1


def test_listen_msg_survives_errors(monkeypatch):
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)

    def boom(data):
        raise RuntimeError("boom")

    monkeypatch.setattr(helper, "get_ac_data", boom)

    gw._listen_to_msg(b"whatever")  # must not raise


def test_thread_main_survives_iteration_errors():
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    gw._connect_retry_delay = 0
    calls = {"n": 0}

    def boom(self):
        calls["n"] += 1
        raise RuntimeError("boom")

    gw._listener_iteration = lambda: boom(gw)
    gw._listening = True
    thread = threading.Thread(target=gw.thread_main)
    thread.start()

    deadline = time.time() + 5
    while calls["n"] < 3 and time.time() < deadline:
        time.sleep(0.01)

    gw._listening = False
    thread.join(2)

    assert calls["n"] >= 3
    assert not thread.is_alive()
    assert gw._listener_alive is False


def test_probe_only_when_stale(monkeypatch):
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    sent = []

    def fake_query():
        sent.append(1)
        return True

    monkeypatch.setattr(gw, "query_all_status", fake_query)

    now = time.monotonic()
    gw._last_seen = now - 10
    gw._maybe_probe()
    assert sent == []

    gw._last_seen = now - 120
    gw._maybe_probe()
    assert sent == [1]
    assert gw._probe_pending is True


def test_probe_failures_force_reconnect(monkeypatch):
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    now = time.monotonic()
    gw._last_seen = now - 120
    gw._max_probe_failures = 2
    reconnected = []

    monkeypatch.setattr(gw, "query_all_status", lambda: False)
    monkeypatch.setattr(
        gw, "_try_reconnect_socket", lambda *args: reconnected.append(1)
    )

    gw._maybe_probe()
    gw._maybe_probe()

    assert gw._probe_failures == 0
    assert len(reconnected) == 1


def test_probe_without_response_escalates(monkeypatch):
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    now = time.monotonic()
    gw._last_seen = now - 120
    gw._max_probe_failures = 1
    reconnected = []

    monkeypatch.setattr(
        gw, "_try_reconnect_socket", lambda *args: reconnected.append(1)
    )

    gw._probe_pending = True
    gw._probe_sent_at = now - 60
    gw._maybe_probe()

    assert gw._probe_pending is False
    assert gw._probe_failures == 0
    assert len(reconnected) == 1


def test_connected_property():
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    gw._listening = True
    gw._listener_alive = True
    gw.sock = FakeSocket()

    now = time.monotonic()
    gw._last_seen = now - 10
    assert gw.connected is True

    gw._last_seen = now - 400
    assert gw.connected is False

    gw._last_seen = None
    assert gw.connected is True

    gw._listener_alive = False
    assert gw.connected is False


def test_update_returns_send_result(monkeypatch):
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    hvac = HVAC(gw=gw, addr_out=1, addr_in=1)
    monkeypatch.setattr(gw, "query_status", lambda addr: False)

    assert hvac.update() is False


def test_control_methods_return_send_result(monkeypatch):
    gw = ZhongHongGateway(ip_addr=LOCAL_HOST, port=LOCAL_PORT, gw_addr=1)
    hvac = HVAC(gw=gw, addr_out=1, addr_in=1)
    monkeypatch.setattr(gw, "send", lambda data: False)

    assert hvac.turn_on() is False
    assert hvac.turn_off() is False
    assert hvac.set_temperature(24) is False
    assert hvac.set_operation_mode("COOL") is False
    assert hvac.set_fan_mode("LOW") is False
