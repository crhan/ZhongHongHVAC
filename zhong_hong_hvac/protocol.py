import collections
import enum
import logging
import struct
from functools import reduce
from typing import List

import attr

logger = logging.getLogger(__name__)


class ChecksumError(Exception):
    pass


class FuncCode(enum.Enum):
    STATUS = 0x50


class CtlStatus(enum.Enum):
    ONE = 0x01
    MULTI = 0x0f
    ONLINE = 0x0f
    ALL = 0xff


class StatusSwitch(enum.Enum):
    ON = 0x01
    OFF = 0x00
    OFF2 = 0x02


class StatusMode(enum.Enum):
    COLD = 0x01
    DEHUMIDIFY = 0x02
    BLAST = 0x04
    HEAT = 0x08


class StatusFanSpeed(enum.Enum):
    HIGH = 0x01
    MID = 0x02
    LOW = 0x04


def validate(data):
    payload_lenth = len(data) - 1
    check_sum = sum(struct.unpack('B' * payload_lenth, data[:-1])) % 256
    return check_sum == struct.unpack('B', data[-1:])[0]


@attr.s
class ZhongHongDataStruct:
    @staticmethod
    def _to_int(element):
        if isinstance(element, enum.Enum):
            return int(element.value)
        return int(element)

    def export(self):
        return list(map(self._to_int, attr.astuple(self)))

    @staticmethod
    def _sum(init, element):
        if isinstance(element, enum.Enum):
            element = element.value
        return init + element

    @property
    def checksum(self):
        return reduce(self._sum, self.export(), 0) % 256

    def encode(self):
        length = len(self.export())
        return struct.pack("B" * length, *self.export())


@attr.s(slots=True, hash=True)
class Header(ZhongHongDataStruct):
    gw_addr = attr.ib()
    _func_code = attr.ib()
    _ctl_code = attr.ib()
    ac_num = attr.ib()

    @property
    def func_code(self):
        return FuncCode(self._func_code)

    @property
    def ctl_code(self):
        if self.func_code == FuncCode.STATUS:
            return CtlStatus(self._ctl_code)
        return None

    def __str__(self):
        return "Header: gw_addr %s, func: %s, ctl: %s, ac_num: %s" % (
            self.gw_addr, self.func_code, self.ctl_code, self.ac_num)


@attr.s(slots=True, hash=True)
class AcAddr(ZhongHongDataStruct):
    addr_out = attr.ib()
    addr_in = attr.ib()

    def __str__(self):
        return "AC %s-%s" % (self.addr_out, self.addr_in)


@attr.s(slots=True)
class AcStatus(ZhongHongDataStruct):
    addr_out = attr.ib()
    addr_in = attr.ib()
    _switch_status = attr.ib()
    target_temperature = attr.ib()
    _mode = attr.ib()
    _fan_speed = attr.ib()
    room_temperature = attr.ib()
    error_code = attr.ib()
    padding1 = attr.ib()
    padding2 = attr.ib()

    @property
    def ac_addr(self):
        return AcAddr(self.addr_out, self.addr_in)

    @property
    def switch_status(self):
        return StatusSwitch(self._switch_status)

    @property
    def mode(self):
        return StatusMode(self._mode)

    @property
    def fan_speed(self):
        return StatusFanSpeed(self._fan_speed)

    def __str__(self):
        return "AC %s-%s power %s, mode %s, speed %s, target_temp %s, room_temp %s" % (
            self.addr_out, self.addr_in, self.switch_status, self.mode,
            self.fan_speed, self.target_temperature, self.room_temperature)


@attr.s(slots=True)
class AcData(object):
    header = attr.ib(init=False)  # type: Header
    payload = attr.ib(
        attr.Factory(collections.deque),
        init=False)  # type: List[ZhongHongDataStruct]

    def add(self, data):
        self.payload.append(data)

    def __str__(self):
        return '\n'.join([str(self.header)] + [str(x) for x in self.payload])

    @property
    def checksum(self):
        return (self.header.checksum +
                sum([item.checksum for item in self.payload])) % 256

    @property
    def bin_checksum(self):
        return struct.pack('B', self.checksum)

    def encode(self):
        return b''.join([self.header.encode()] +
                        [x.encode()
                         for x in self.payload] + [self.bin_checksum])


def parse_data(data):
    if not validate(data):
        raise ChecksumError("checksum error")

    ac_data = AcData()
    ac_data.header = header = Header(*struct.unpack("BBBB", data[:4]))
    logger.debug(str(header))

    if header.func_code == FuncCode.STATUS:
        if header.ctl_code == CtlStatus.ONE:
            ac_status = AcStatus(*struct.unpack('B' * 10, data[4:14]))
            logger.debug(ac_status.export())
            logger.debug(str(ac_status))
            ac_data.add(ac_status)
        elif header.ctl_code == CtlStatus.ALL:
            for idx in range(header.ac_num):
                start = 4 + idx * 10
                end = 4 + (idx + 1) * 10
                ac_data.add(
                    AcStatus(*struct.unpack('B' * 10, data[start:end])))
        else:
            raise TypeError("not support type: %s" % header)
    else:
        raise TypeError("not support type: %s" % header)

    return ac_data
