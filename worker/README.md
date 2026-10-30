# worker/

Event-driven workers. `media/` frames and audio from a replay file (ffmpeg), `vision/` frames → `product_seen`, `voice/` audio → `speech`, `brain/` cards, comment labels, temporal references.

`cohost/` is a separate, default-off replay/demo speech module. It consumes explicit approved snapshots and host/device signals, has no commerce write path, and is not connected to the worker lanes. See [its run guide and limitations](cohost/README.md).
