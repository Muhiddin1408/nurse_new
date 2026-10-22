package events

import (
	"bytes"
	"embed"
	"encoding/json"
	"fmt"
	"path"
	"strings"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

//go:embed schemas/*.json
var schemaFS embed.FS

// SchemaError — payload kontraktga mos emas.
type SchemaError struct {
	EventType string
	Detail    string
}

func (e *SchemaError) Error() string { return e.EventType + ": " + e.Detail }

var (
	compileOnce sync.Once
	compileErr  error
	schemaIDs   map[string]string // fayl nomi → $id
	compiler    *jsonschema.Compiler
	compiled    sync.Map // event_type → *jsonschema.Schema
	compileMu   sync.Mutex
)

func loadSchemas() {
	compiler = jsonschema.NewCompiler()
	compiler.AssertFormat()
	schemaIDs = map[string]string{}
	entries, err := schemaFS.ReadDir("schemas")
	if err != nil {
		compileErr = err
		return
	}
	for _, e := range entries {
		data, err := schemaFS.ReadFile(path.Join("schemas", e.Name()))
		if err != nil {
			compileErr = err
			return
		}
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
		if err != nil {
			compileErr = fmt.Errorf("%s: %w", e.Name(), err)
			return
		}
		id, _ := doc.(map[string]any)["$id"].(string)
		if id == "" {
			compileErr = fmt.Errorf("%s: $id yo'q", e.Name())
			return
		}
		if err := compiler.AddResource(id, doc); err != nil {
			compileErr = err
			return
		}
		schemaIDs[e.Name()] = id
	}
}

func schemaFor(eventType string) (*jsonschema.Schema, error) {
	if s, ok := compiled.Load(eventType); ok {
		return s.(*jsonschema.Schema), nil
	}
	compileOnce.Do(loadSchemas)
	if compileErr != nil {
		return nil, compileErr
	}
	spec, ok := Lookup(eventType)
	if !ok {
		return nil, &SchemaError{EventType: eventType, Detail: "katalogda yo'q event"}
	}
	compileMu.Lock()
	defer compileMu.Unlock()
	sch, err := compiler.Compile(schemaIDs[spec.Schema])
	if err != nil {
		return nil, err
	}
	compiled.Store(eventType, sch)
	return sch, nil
}

// ValidateJSON — butun envelope (data bilan) sxemaga mosligini tekshiradi.
func ValidateJSON(envelope []byte) error {
	var head struct {
		EventType string `json:"event_type"`
	}
	if err := json.Unmarshal(envelope, &head); err != nil || head.EventType == "" {
		return &SchemaError{EventType: head.EventType, Detail: "event_type yo'q yoki JSON yaroqsiz"}
	}
	sch, err := schemaFor(head.EventType)
	if err != nil {
		return err
	}
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(envelope))
	if err != nil {
		return &SchemaError{EventType: head.EventType, Detail: err.Error()}
	}
	if err := sch.Validate(inst); err != nil {
		return &SchemaError{EventType: head.EventType, Detail: strings.TrimSpace(err.Error())}
	}
	return nil
}
