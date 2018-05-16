import json
import logging
import socket
from typing import Callable, List
import enum

from . import hub, protocol

logger = logging.getLogger(__name__)


class HVAC:
    def __init__(self, gw: hub.ZhongHongGateway, addr_out: int, addr_in: int):
        self.gw = gw
        self.addr_out = addr_out
        self.addr_in = addr_in
        self.ac_addr = protocol.AcAddr(self.addr_out, self.addr_in)
        self.gw.add_status_callback(self.ac_addr, self._status_update)
        self.status_callback = []  # type: List[Callable]

        self.switch_status = None
        self.target_temperature = None
        self.current_operation = None
        self.current_fan_mode = None
        self.current_temperature = None
        self.error_code = None

    def _status_update(self, ac_status: protocol.AcStatus) -> bool:
        assert self.ac_addr == ac_status.ac_addr
        for _attr in ("switch_status", "target_temperature",
                      "current_operation", "current_fan_mode",
                      "current_temperature", "error_code"):
            value = getattr(ac_status, _attr)
            if isinstance(value, enum.Enum):
                value = value.name
            setattr(self, _attr, value)

        logger.info("[callback]hvac %s status updated: %s", self.ac_addr,
                    self.status())
        for func in self.status_callback:
            if callable(func):
                func(self)

    def register_update_callback(self, _callable: Callable) -> bool:
        if callable(_callable):
            self.status_callback.append(_callable)
            return True
        return False

    def send(self, ac_data: protocol.AcData) -> None:
        self.gw.send(ac_data)

    def update(self) -> bool:
        message = protocol.AcData()
        message.header = protocol.Header(
            self.gw_addr, protocol.FuncCode.STATUS, protocol.CtlStatus.ONE, 1)
        message.add(self.ac_addr)
        self.gw.query_status(self.ac_addr)
        return True

    def status(self):
        return json.dumps({
            "switch_status": self.switch_status,
            "target_temperature": self.target_temperature,
            "current_operation": self.current_operation,
            "current_fan_mode": self.current_fan_mode,
            "current_temperature": self.current_temperature,
            "error_code": self.error_code
        })

    @property
    def operation_list(self):
        return [x.name for x in list(protocol.StatusOperation)]

    @property
    def fan_list(self):
        return [x.name for x in list(protocol.StatusFanMode)]

    @property
    def gw_addr(self):
        return self.gw.gw_addr

    @property
    def is_on(self):
        return self.switch_status == protocol.StatusSwitch.ON.name

    @property
    def min_temp(self):
        return 16

    @property
    def max_temp(self):
        return 30
