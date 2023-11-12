import enum
import json
import logging
import socket
from typing import Callable, List

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
        self.gw.add_device(self)

    def _call_status_update(self):
        for func in self.status_callback:
            if callable(func):
                func(self)

    def _status_update(self, ac_status: protocol.AcStatus) -> bool:
        assert self.ac_addr == ac_status.ac_addr
        dirty = False
        for _attr in (
            "switch_status",
            "target_temperature",
            "current_operation",
            "current_fan_mode",
            "current_temperature",
            "error_code",
        ):
            value = getattr(ac_status, _attr)
            if isinstance(value, enum.Enum):
                value = value.name
            if getattr(self, _attr) != value:
                setattr(self, _attr, value)
                dirty = True

        if dirty:
            logger.debug(
                "[callback]hvac %s status updated: %s", self.ac_addr, self.status()
            )
            self._call_status_update()
        else:
            logger.debug(
                "[callback]hvac %s status remains the same: %s",
                self.ac_addr,
                self.status(),
            )

    def set_attr(self, func_code, value) -> bool:
        if func_code == protocol.FuncCode.CTL_POWER:
            self.switch_status = value.name
        elif func_code == protocol.FuncCode.CTL_TEMPERATURE:
            self.target_temperature = value
        elif func_code == protocol.FuncCode.CTL_OPERATION:
            self.current_operation = value.name
        elif func_code == protocol.FuncCode.CTL_FAN_MODE:
            self.current_fan_mode = value.name
        self._call_status_update()

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
            self.gw_addr, protocol.FuncCode.STATUS, protocol.CtlStatus.ONE, 1
        )
        message.add(self.ac_addr)
        self.gw.query_status(self.ac_addr)
        return True

    def status(self):
        return json.dumps(
            {
                "switch_status": self.switch_status,
                "target_temperature": self.target_temperature,
                "current_operation": self.current_operation,
                "current_fan_mode": self.current_fan_mode,
                "current_temperature": self.current_temperature,
                "error_code": self.error_code,
            }
        )

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

    def _ctrl_ac(self, func_code, ctrl_code):
        request_data = protocol.AcData()
        request_data.header = protocol.Header(
            self.gw_addr, func_code, ctrl_code, protocol.CtlStatus.ONE
        )
        request_data.add(self.ac_addr)
        self.send(request_data)

    def turn_on(self) -> None:
        self._ctrl_ac(protocol.FuncCode.CTL_POWER, protocol.StatusSwitch.ON)

    def turn_off(self) -> None:
        self._ctrl_ac(protocol.FuncCode.CTL_POWER, protocol.StatusSwitch.OFF)

    def set_temperature(self, temperature: str) -> None:
        self._ctrl_ac(protocol.FuncCode.CTL_TEMPERATURE, temperature)

    def set_fan_mode(self, fan_mode: str) -> None:
        self._ctrl_ac(protocol.FuncCode.CTL_FAN_MODE, protocol.StatusFanMode[fan_mode])

    def set_operation_mode(self, operation_mode: str) -> None:
        self._ctrl_ac(
            protocol.FuncCode.CTL_OPERATION, protocol.StatusOperation[operation_mode]
        )
