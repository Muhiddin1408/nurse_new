import json
import logging

from medbron_common.context import request_id_var
from medbron_common.logging import JsonFormatter, mask_pii


def test_mask_pii() -> None:
    assert mask_pii("tel +998901234567 ok") == "tel +99890***4567 ok"
    assert mask_pii("998901234567") == "99890***4567"
    assert mask_pii("card 8600123412341234") == "card ************1234"
    assert mask_pii("order 12345") == "order 12345"


def test_json_formatter_fields_and_masking() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "sms to %s", ("+998901234567",), None)
    record.phone = "+998901234567"
    token = request_id_var.set("rid")
    try:
        out = json.loads(JsonFormatter("svc").format(record))
    finally:
        request_id_var.reset(token)
    assert out["service"] == "svc"
    assert out["level"] == "INFO"
    assert out["request_id"] == "rid"
    assert out["msg"] == "sms to +99890***4567"
    assert out["phone"] == "+99890***4567"
    assert {"ts", "trace_id"} <= out.keys()
