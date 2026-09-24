from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Channel(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHANNEL_UNSPECIFIED: _ClassVar[Channel]
    CHANNEL_SMS: _ClassVar[Channel]
    CHANNEL_PUSH: _ClassVar[Channel]
CHANNEL_UNSPECIFIED: Channel
CHANNEL_SMS: Channel
CHANNEL_PUSH: Channel

class SendRequest(_message.Message):
    __slots__ = ("idempotency_key", "channel", "recipient", "template", "params", "language")
    class ParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    RECIPIENT_FIELD_NUMBER: _ClassVar[int]
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    idempotency_key: str
    channel: Channel
    recipient: str
    template: str
    params: _containers.ScalarMap[str, str]
    language: str
    def __init__(self, idempotency_key: _Optional[str] = ..., channel: _Optional[_Union[Channel, str]] = ..., recipient: _Optional[str] = ..., template: _Optional[str] = ..., params: _Optional[_Mapping[str, str]] = ..., language: _Optional[str] = ...) -> None: ...

class SendResponse(_message.Message):
    __slots__ = ("message_id",)
    MESSAGE_ID_FIELD_NUMBER: _ClassVar[int]
    message_id: str
    def __init__(self, message_id: _Optional[str] = ...) -> None: ...
