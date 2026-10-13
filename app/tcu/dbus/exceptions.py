"""D-Bus adapter and transport errors."""


class TCUError(Exception):
    code = "tcu_error"
    status_code = 500


class TransportNotConfigured(TCUError):
    code = "transport_not_configured"
    status_code = 503


class TCUNotConnected(TCUError):
    code = "not_connected"
    status_code = 503


class TCUTimeout(TCUError):
    code = "timeout"
    status_code = 504


class DbusTargetNotFound(TCUError):
    code = "dbus_target_not_found"
    status_code = 404


class DbusRemoteError(TCUError):
    code = "dbus_method_error"
    status_code = 502

    def __init__(self, name: str, message: str) -> None:
        self.name = name
        super().__init__(f"{name}: {message}" if name else message)


class InvalidDBusArguments(TCUError):
    code = "invalid_arguments"
    status_code = 400
