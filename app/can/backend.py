"""python-can bus factory."""

from __future__ import annotations

import can

from models import CANProfile


def open_bus(profile: CANProfile):
    """Open one configured python-can interface; never called per UI client."""
    config = {
        "interface": profile.interface,
        "channel": profile.channel,
        "bitrate": profile.bitrate,
        "fd": profile.fd,
        "receive_own_messages": False,
    }
    if profile.fd:
        config["data_bitrate"] = profile.data_bitrate
    return can.Bus(**config)
