// Package reqctx — so'rov konteksti: X-Request-ID log, chiquvchi HTTP va eventlarga uzatiladi.
package reqctx

import "context"

type key int

const requestIDKey key = iota

// WithRequestID kontekstga request id qo'shadi.
func WithRequestID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, requestIDKey, id)
}

// RequestID kontekstdagi request id (bo'lmasa "").
func RequestID(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey).(string)
	return id
}
