"""python-can bus factory."""

from __future__ import annotations

import can

from .models import CANProfile


def open_bus(profile: CANProfile):
    """Open one configured python-can interface; never called per UI client."""
    config = {
        "interface": profile.interface,
        "channel": profile.channel,
        "receive_own_messages": False,
    }
    if profile.interface != "virtual":
        config["bitrate"] = profile.bitrate
    if profile.fd and profile.interface != "virtual":
        config["fd"] = True
        config["data_bitrate"] = profile.data_bitrate
    return can.Bus(**config)
