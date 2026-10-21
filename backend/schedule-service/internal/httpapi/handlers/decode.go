package handlers

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"

	gchttp "github.com/Muhiddin1408/nurse_new/libs/go-common/httpapi"
)

// decodeTwice — PATCH uchun: tipli struktura va "qaysi kalit yuborilgan" xaritasi (null ≠ yo'q).
func decodeTwice(w http.ResponseWriter, r *http.Request, typed any, raw *map[string]any) error {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 1<<20))
	if err != nil {
		return gchttp.BadRequest("INVALID_JSON", "so'rov tanasi o'qilmadi")
	}
	r.Body = io.NopCloser(bytes.NewReader(body))
	if err := gchttp.DecodeJSON(w, r, typed); err != nil {
		return err
	}
	if err := json.Unmarshal(body, raw); err != nil {
		return gchttp.BadRequest("INVALID_JSON", "JSON obyekt kutilgan")
	}
	return nil
}
