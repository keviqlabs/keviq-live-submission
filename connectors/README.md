# connectors/

`sim/`: simulator — timed JSONL comments, fake Messenger webhook/Send API with the same interface as the real one. `facebook/`: read-only Graph API comment source for one live video (`comments.py`) and the `live-ingest` entry point (`ingest.py`); GET only, no Messenger send, no hide.
