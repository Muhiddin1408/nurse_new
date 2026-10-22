// Package telemetry — OpenTelemetry tracing. OTEL_EXPORTER_OTLP_ENDPOINT bo'sh bo'lsa — no-op,
// lekin W3C traceparent propagatsiyasi baribir yoqiladi (kiruvchi trace uziladigan bo'lmasin).
package telemetry

import (
	"context"
	"net/http"
	"strings"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
)

func init() {
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{},
	))
}

// Setup — tracing'ni yoqadi. Qaytaradi: shutdown funksiyasi (har doim nil emas).
func Setup(ctx context.Context, service, environment, endpoint string) (func(context.Context) error, error) {
	if endpoint == "" {
		return func(context.Context) error { return nil }, nil
	}
	endpoint = strings.TrimPrefix(strings.TrimPrefix(endpoint, "http://"), "https://")
	exp, err := otlptracegrpc.New(ctx, otlptracegrpc.WithEndpoint(endpoint), otlptracegrpc.WithInsecure())
	if err != nil {
		return nil, err
	}
	res := resource.NewWithAttributes(semconv.SchemaURL,
		semconv.ServiceName(service), semconv.DeploymentEnvironment(environment))
	tp := sdktrace.NewTracerProvider(sdktrace.WithBatcher(exp), sdktrace.WithResource(res))
	otel.SetTracerProvider(tp)
	return tp.Shutdown, nil
}

// InjectHTTP — chiquvchi so'rovga traceparent.
func InjectHTTP(ctx context.Context, h http.Header) {
	otel.GetTextMapPropagator().Inject(ctx, propagation.HeaderCarrier(h))
}

// Traceparent — joriy span konteksti (bo'lmasa "").
func Traceparent(ctx context.Context) string {
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	return carrier["traceparent"]
}

// ExtractTraceparent — kiruvchi AMQP/HTTP header'dagi traceparent'ni kontekstga.
func ExtractTraceparent(ctx context.Context, traceparent string) context.Context {
	if traceparent == "" {
		return ctx
	}
	return otel.GetTextMapPropagator().Extract(ctx, propagation.MapCarrier{"traceparent": traceparent})
}
