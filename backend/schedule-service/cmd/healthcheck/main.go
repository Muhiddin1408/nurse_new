// healthcheck — distroless image'da shell/curl yo'q; Docker HEALTHCHECK shu binarni chaqiradi.
//
//	/healthcheck http://127.0.0.1:8080/health/live
package main

import (
	"context"
	"net/http"
	"os"
	"time"
)

func main() {
	url := "http://127.0.0.1:8080/health/live"
	if len(os.Args) > 1 {
		url = os.Args[1]
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	// G704: URL — Docker HEALTHCHECK argumenti (konteyner ichidagi lokal endpoint), foydalanuvchi kiritmaydi
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil) //nolint:gosec
	if err != nil {
		os.Exit(1)
	}
	resp, err := http.DefaultClient.Do(req) //nolint:gosec // yuqoridagi G704 izohi
	if err != nil {
		os.Exit(1)
	}
	_ = resp.Body.Close()
	if resp.StatusCode >= 400 {
		os.Exit(1)
	}
}
