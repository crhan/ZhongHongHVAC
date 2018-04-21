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
        self.current_operation = None
        self.current_fan_mode = None
        self.current_temperature = None
        self.error_code = None

    def _status_update(self, ac_status: protocol.AcStatus) -> bool:
        assert self.ac_addr == ac_status.ac_addr
        for _attr in ("switch_status", "target_temperature", "current_operation",
                      "current_fan_mode", "current_temperature", "error_code"):
            setattr(self, _attr, getattr(ac_status, _attr))

        logger.info("[callback]hvac %s status updated: %s", self.ac_addr, self.status())

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
            "switch_status": self.switch_status.name,
            "target_temperature": self.target_temperature,
            "current_operation": self.current_operation.name,
            "current_fan_mode": self.current_fan_mode.name,
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
        return self.switch_status == protocol.StatusSwitch.ON

    @property
    def min_temp(self):
        return 16

    @property
    def max_temp(self):
        return 30
