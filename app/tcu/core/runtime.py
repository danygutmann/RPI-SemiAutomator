"""Shared process-level TCU services used by the API and GUI."""

from .config import load_config
from .service import TCUService
from ..dbus.client import DBusClient

config = load_config()
tcu_service = TCUService(config=config, client=DBusClient(config))
