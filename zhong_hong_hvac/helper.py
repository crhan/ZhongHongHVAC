import copy
import logging
import struct
import socket

from .protocol import (AcData, AcStatus, ChecksumError, CtlStatus, FuncCode,
                       Header)

logger = logging.getLogger(__name__)


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


def get_data_frame(data):
    """find frame in raw data.

    Arguments:
        data {bytes} -- raw bytes read from wire

    Yields:
        {bytes} -- a valid frame (checksum checked)

    Returns:
        None -- [description]
    """
    data = copy.copy(data)
    while data:
        try:
            if len(data) <= 5:
                return data
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
            return data

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


def get_ac_data(data: bytes) -> AcData:
    for data_frame in get_data_frame(data):
        yield parse_data(data_frame)
