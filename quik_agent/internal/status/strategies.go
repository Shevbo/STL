package status

import (
	"encoding/json"
	"os"
)

// loadStrategyDoc reads strategies_doc.json (Task 10 populates its content;
// this loader only needs the file's shape: a JSON object keyed by strategy
// id, e.g. {"fvg": {"description": "...", ...}}) and returns the raw doc for
// id verbatim, so a Task 10 schema change never requires touching this
// loader. Read fresh every call: the file is small and edited rarely, and a
// long-running agent must pick up a doc update without a restart.
func loadStrategyDoc(path, id string) (json.RawMessage, bool, error) {
	if path == "" {
		return nil, false, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, false, nil
		}
		return nil, false, err
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, false, err
	}
	doc, ok := m[id]
	return doc, ok, nil
}
