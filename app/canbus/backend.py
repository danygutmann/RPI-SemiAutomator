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
    if profile.interface == "kvaser":
        try:
            config["channel"] = int(profile.channel)
        except ValueError as exc:
            raise ValueError("Kvaser channel must be a numeric channel index") from exc
    if profile.interface != "virtual":
        config["bitrate"] = profile.bitrate
    if profile.fd and profile.interface != "virtual":
        config["fd"] = True
        config["data_bitrate"] = profile.data_bitrate
    if profile.listen_only and profile.interface == "vector":
        config["listen_only"] = True
    if profile.interface == "pcan":
        config["state"] = can.BusState.PASSIVE if profile.listen_only else can.BusState.ACTIVE
    return can.Bus(**config)
