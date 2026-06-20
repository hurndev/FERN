class FernError(Exception):
    pass


class VerificationError(FernError):
    pass


class MalformedEventError(VerificationError):
    pass


class InvalidHashError(VerificationError):
    pass


class InvalidSignatureError(VerificationError):
    pass


class SerializationError(FernError):
    pass


class StorageError(FernError):
    pass


class TransportError(FernError):
    pass


class AuthorizationError(FernError):
    pass
