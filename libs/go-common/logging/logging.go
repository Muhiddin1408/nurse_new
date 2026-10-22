// Package logging — strukturali JSON log (BLUEPRINT 11): ts, level, service, request_id, trace_id, msg.
// Telefon va karta raqamlari yozilishdan OLDIN maskalanadi.
package logging

import (
	"context"
	"io"
	"log/slog"
	"os"
	"regexp"
	"strings"

	"go.opentelemetry.io/otel/trace"

	"github.com/Muhiddin1408/nurse_new/libs/go-common/reqctx"
)

var (
	phoneRe = regexp.MustCompile(`\+?998\d{9}`)
	cardRe  = regexp.MustCompile(`\d{16}`)
)

func isDigit(b byte) bool { return b >= '0' && b <= '9' }

// replaceBounded — Go regexp'da lookbehind yo'q: moslik atrofida raqam bo'lmasligini qo'lda tekshiramiz.
func replaceBounded(s string, re *regexp.Regexp, mask func(string) string) string {
	idx := re.FindAllStringIndex(s, -1)
	if idx == nil {
		return s
	}
	var b strings.Builder
	last := 0
	for _, m := range idx {
		start, end := m[0], m[1]
		if (start > 0 && isDigit(s[start-1])) || (end < len(s) && isDigit(s[end])) {
			continue
		}
		b.WriteString(s[last:start])
		b.WriteString(mask(s[start:end]))
		last = end
	}
	b.WriteString(s[last:])
	return b.String()
}

// MaskPII: "+998901234567" → "+99890***4567", 16 xonali karta → "************1234".
func MaskPII(s string) string {
	s = replaceBounded(s, phoneRe, func(m string) string {
		return m[:len(m)-7] + "***" + m[len(m)-4:]
	})
	return replaceBounded(s, cardRe, func(m string) string {
		return strings.Repeat("*", 12) + m[12:]
	})
}

type contextHandler struct {
	slog.Handler
}

func (h contextHandler) Handle(ctx context.Context, r slog.Record) error {
	if id := reqctx.RequestID(ctx); id != "" {
		r.AddAttrs(slog.String("request_id", id))
	}
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r.AddAttrs(slog.String("trace_id", sc.TraceID().String()))
	}
	r.Message = MaskPII(r.Message)
	return h.Handler.Handle(ctx, r)
}

func (h contextHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return contextHandler{h.Handler.WithAttrs(attrs)}
}

func (h contextHandler) WithGroup(name string) slog.Handler {
	return contextHandler{h.Handler.WithGroup(name)}
}

func parseLevel(level string) slog.Level {
	var l slog.Level
	if err := l.UnmarshalText([]byte(strings.ToUpper(level))); err != nil {
		return slog.LevelInfo
	}
	return l
}

// NewWithWriter — testlar uchun.
func NewWithWriter(w io.Writer, service, level string) *slog.Logger {
	h := slog.NewJSONHandler(w, &slog.HandlerOptions{
		Level: parseLevel(level),
		ReplaceAttr: func(_ []string, a slog.Attr) slog.Attr {
			switch {
			case a.Key == slog.TimeKey:
				a.Key = "ts"
			case a.Value.Kind() == slog.KindString:
				a.Value = slog.StringValue(MaskPII(a.Value.String()))
			}
			return a
		},
	})
	return slog.New(contextHandler{h}).With(slog.String("service", service))
}

// New — stdout ga JSON log va uni slog.Default qiladi.
func New(service, level string) *slog.Logger {
	logger := NewWithWriter(os.Stdout, service, level)
	slog.SetDefault(logger)
	return logger
}
