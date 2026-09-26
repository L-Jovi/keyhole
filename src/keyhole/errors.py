"""One error type: a stable machine-readable code plus a message for humans and models."""


class KeyholeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def require(condition: object, code: str, message: str) -> None:
    if not condition:
        raise KeyholeError(code, message)
