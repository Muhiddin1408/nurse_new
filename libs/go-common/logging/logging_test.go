package logging

import (
	"bytes"
	"context"
	"encoding/json"
	"testing"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/reqctx"
)

func TestMaskPII(t *testing.T) {
	cases := map[string]string{
		"tel +998901234567 ok":  "tel +99890***4567 ok",
		"998901234567":          "99890***4567",
		"card 8600123412341234": "card ************1234",
		"order 12345":           "order 12345",
		"x19989012345678":       "x19989012345678", // atrofida raqam — telefon emas
	}
	for in, want := range cases {
		if got := MaskPII(in); got != want {
			t.Errorf("MaskPII(%q)=%q want %q", in, got, want)
		}
	}
}

func TestJSONFields(t *testing.T) {
	var buf bytes.Buffer
	log := NewWithWriter(&buf, "svc", "info")
	ctx := reqctx.WithRequestID(context.Background(), "rid")
	log.InfoContext(ctx, "sms to +998901234567", "phone", "+998901234567")
	var out map[string]any
	if err := json.Unmarshal(buf.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	if out["service"] != "svc" || out["request_id"] != "rid" || out["msg"] != "sms to +99890***4567" ||
		out["phone"] != "+99890***4567" || out["ts"] == nil || out["level"] != "INFO" {
		t.Fatalf("unexpected %v", out)
	}
}
