"""
本地测试用

from @ruohan.chen
"""

from zhong_hong_hvac.hub import ZhongHongGateway
from zhong_hong_hvac.hvac import HVAC

LOCAL_PORT = 9999
LOCAL_HOST = "192.168.15.19"


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
