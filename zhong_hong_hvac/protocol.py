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
    CTL_POWER = 0x31
    CTL_TEMP = 0x32
    CTL_MODE = 0x33
    CTL_FAN_SPEED = 0x34


class CtlStatus(enum.Enum):
    ONE = 0x01
    MULTI = 0x0f
    ONLINE = 0x0f
    ALL = 0xff


class StatusSwitch(enum.Enum):
    ON = 0x01
    OFF = 0x00

    @classmethod
    def new_status_switch(cls, value):
        return cls(value % 2)


class StatusMode(enum.Enum):
    COLD = 0x01
    DEHUMIDIFY = 0x02
    BLAST = 0x04
    HEAT = 0x08


class StatusFanSpeed(enum.Enum):
    HIGH = 0x01
    MID = 0x02
    LOW = 0x04


STATUS_PAYLOAD_LEN = 10
STATUS_ONLINE_PAYLOAD_LEN = 3
AC_ADDR_LEN = 2


def validate(data_frame):
    """checksum of data frame."""
    header = Header.get_header_from_frame(data_frame)
    if not header.is_valid:
        return False

    data_checksum = sum(
        struct.unpack('B' * header.checksum_position,
                      data_frame[:header.checksum_position])) % 256
    pos = header.checksum_position
    data_checksum = struct.unpack('B', data_frame[pos:pos + 1])[0]
    return data_checksum == data_checksum


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
        return init + ZhongHongDataStruct._to_int(element)

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
    def is_valid(self):
        try:
            self.check()
        except ValueError:
            logger.debug("header not valid: %s", self.export())
            return False
        return True

    def check(self):
        self.func_code
        self.ctl_code

    @classmethod
    def get_header_from_frame(cls, data_frame):
        if len(data_frame) < 4:
            return None
        return cls(*struct.unpack("BBBB", data_frame[:4]))

    @property
    def func_code(self):
        return FuncCode(self._func_code)

    @property
    def ctl_code(self):
        if self.func_code == FuncCode.STATUS:
            return CtlStatus(self._ctl_code)
        elif self.func_code == FuncCode.CTL_POWER:
            return StatusSwitch(self._ctl_code)
        elif self.func_code == FuncCode.CTL_TEMP:
            return self._ctl_code
        elif self.func_code == FuncCode.CTL_FAN_SPEED:
            return StatusFanSpeed(self._ctl_code)
        return None

    def __str__(self):
        return "Header: gw_addr %s, func: %s, ctl: %s, ac_num: %s" % (
            self.gw_addr, self.func_code, self.ctl_code, self.ac_num)

    def is_status_update(self):
        if self.func_code != FuncCode.STATUS:
            return False
        if self.ctl_code not in (CtlStatus.ALL, CtlStatus.ONE,
                                 CtlStatus.MULTI):
            return False
        return True

    @property
    def length(self):
        return 4

    @property
    def payload_length(self):
        if self.func_code == FuncCode.STATUS:
            if self.ctl_code in (CtlStatus.ONE, CtlStatus.MULTI,
                                 CtlStatus.ALL):
                payload_length = STATUS_PAYLOAD_LEN * self.ac_num
            elif self.ctl_code in (CtlStatus.ONLINE):
                payload_length = STATUS_ONLINE_PAYLOAD_LEN * self.ac_num
            else:
                raise Exception("unknown ctrl code: %s", self.header.export())
        elif self.func_code in (FuncCode.CTL_POWER, FuncCode.CTL_TEMP,
                                FuncCode.CTL_MODE, FuncCode.CTL_FAN_SPEED):
            payload_length = AcAddr * self.ac_num
        else:
            raise Exception("unknown func code: %s", self.header.export())

        return payload_length

    @property
    def checksum_position(self):
        return self.length + self.payload_length


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
    switch_status = attr.ib(converter=StatusSwitch.new_status_switch)
    target_temperature = attr.ib()
    mode = attr.ib(converter=StatusMode)
    fan_speed = attr.ib(converter=StatusFanSpeed)
    room_temperature = attr.ib()
    error_code = attr.ib()
    padding1 = attr.ib()
    padding2 = attr.ib()

    @property
    def ac_addr(self):
        return AcAddr(self.addr_out, self.addr_in)

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
    request = attr.ib(True)

    def add(self, data):
        self.payload.append(data)

    def __str__(self):
        return '\n'.join([str(self.header)] + [str(x) for x in self.payload])

    @property
    def length(self):
        header_length = self.header.length
        checksum_length = 1

        if self.func_code == FuncCode.STATUS:
            if self.ctl_code in (CtlStatus.ONE, CtlStatus.MULTI,
                                 CtlStatus.ALL):
                payload_length = STATUS_PAYLOAD_LEN * self.ac_num
            elif self.ctl_code in (CtlStatus.ONLINE):
                payload_length = STATUS_ONLINE_PAYLOAD_LEN * self.ac_num
            else:
                raise Exception("unknown ctrl code: %s", self.header.export())
        elif self.func_code in (FuncCode.CTL_POWER, FuncCode.CTL_TEMP,
                                FuncCode.CTL_MODE, FuncCode.CTL_FAN_SPEED):
            payload_length = AcAddr * self.ac_num
        else:
            raise Exception("unknown func code: %s", self.header.export())

        return header_length + checksum_length + payload_length

    @property
    def ac_num(self):
        return self.header.ac_num

    @property
    def func_code(self):
        return self.header.func_code

    @property
    def ctl_code(self):
        return self.header.ctl_code

    @property
    def is_request(self):
        '''Is this data a Request or Response.'''
        return self.request

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


def get_data_frame(data):
    """find frame in raw data.

    Arguments:
        data {bytes} -- raw bytes read from wire

    Yields:
        {bytes} -- a valid frame (checksum checked)

    Returns:
        None -- [description]
    """
    while data:
        try:
            if len(data) <= 5:
                return None
            header = Header.get_header_from_frame(data)
            header.check()

        except ValueError as e:
            logger.error("header code unknown: %s", data[:4], exc_info=e)
            data = data[1:]
            continue

        payload_length = header.payload_length
        total_length = header.length + payload_length + 1
        if len(data) < total_length:
            logger.error("date length not enough")
            return None

        date_frame = data[:total_length]
        if validate(date_frame):
            yield date_frame
        else:
            logger.error("checksum error and drop this frame: %s", date_frame)
        data = data[total_length:]


def parse_data(data_frame):
    if not validate(data_frame):
        raise ChecksumError("checksum error")

    ac_data = AcData(request=False)
    ac_data.header = header = Header.get_header_from_frame(data_frame)
    logger.debug(str(header))

    if header.func_code == FuncCode.STATUS:
        if header.ctl_code == CtlStatus.ONE:
            ac_status = AcStatus(*struct.unpack('B' * 10, data_frame[4:14]))
            logger.debug(ac_status.export())
            logger.debug(str(ac_status))
            ac_data.add(ac_status)
        elif header.ctl_code == CtlStatus.ALL:
            for idx in range(header.ac_num):
                start = 4 + idx * 10
                end = 4 + (idx + 1) * 10
                ac_data.add(
                    AcStatus(*struct.unpack('B' * 10, data_frame[start:end])))
        else:
            raise TypeError("not support type: %s" % header)
    else:
        raise TypeError("not support type: %s" % header)

    return ac_data
