import json
import logging
import socket

from . import hub, protocol

logger = logging.getLogger(__name__)


class HVAC:
    def __init__(self, gw: hub.ZhongHongGateway, out_addr: int, in_addr: int):
        self.gw = gw
        self.out_addr = out_addr
        self.in_addr = in_addr
        self.ac_addr = protocol.AcAddr(self.out_addr, self.in_addr)
        self.gw.add_status_callback(self.ac_addr, self._status_update)

        self.switch_status = None
        self.target_temperature = None
        self.mode = None
        self.fan_speed = None
        self.room_temperature = None
        self.error_code = None

    def _status_update(self, ac_status: protocol.AcStatus) -> bool:
        assert self.ac_addr == ac_status.ac_addr
        for _attr in ("switch_status", "target_temperature", "mode",
                      "fan_speed", "room_temperature", "error_code"):
            setattr(self, _attr, getattr(ac_status, _attr))

    def send(self, ac_data: protocol.AcData) -> None:
        self.gw.send(ac_data)

    def update(self) -> bool:
        message = protocol.AcData()
        message.header = protocol.Header(
            self.gw_addr, protocol.FuncCode.STATUS, protocol.CtlStatus.ONE, 1)
        message.add(self.ac_addr)
        if not self.gw.query_status(self.ac_addr):
            logger.error("update hvac status fail: %s", self.ac_addr)
            return False
        return True

    def status(self):
        return json.dumps({
            "switch_status": self.switch_status.name,
            "target_temperature": self.target_temperature,
            "mode": self.mode.name,
            "fan_speed": self.fan_speed.name,
            "room_temperature": self.room_temperature,
            "error_code": self.error_code
        })

    @property
    def gw_addr(self):
        return self.gw.gw_addr
