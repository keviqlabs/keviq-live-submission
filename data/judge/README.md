# Judge data: which files go with which input

Every comment and customer in these files is synthetic. sha256 checksums are given so a copy can be checked with `shasum -a 256 <file>`.

## judge1 (in this repository, no download)

judge1 is the hand-written session `data/sessions/demo1` with product pictures drawn by this repository. The pictures are flat drawings made from polygons and ellipses by `scripts/judge_draw.py`; no photo, font or outside image is used, and the same code gives the same bytes. `scripts/judge1.py build` writes the session files from demo1 and the drawings; `scripts/judge1.py video` (`make judge-video`) draws the 64 s video, which is not stored in Git.

| Input | File | What it is | sha256 |
|---|---|---|---|
| mode 1 replay | `data/sessions/judge1/events.jsonl` | 69 events: 14 comments, 3 speech lines, 4 product sightings and 2 customer replies written by hand, and 46 recorded stand-ins (`fx-`) for the model and rule output, with the drawn pictures | `4322e13af2704a9b5d2197371f2fcd44bbcef6238760eec73520a6dda2c33d9f` |
| mode 1 scoring | `data/sessions/judge1/gold.json` | the answer key of demo1, unchanged | `1a18c535cd815480b3902da6553c38ef6af83071296f1f708ff0426aa4ef0ea6` |
| source of judge1 | `data/sessions/demo1/events.jsonl` | the hand-written session (picture addresses that the console cannot show) | `512af00b4eab7a8e2e5cb790a24ad2b6e5cf3feb5e98bb5c5eb8188024d1cee9` |
| source of judge1 | `data/sessions/demo1/gold.json` | its answer key | `a8e3f2a6b28bf209723f4ad7f92b7402dfaa1a4d820408dfa9b2a949a005c45d` |
| modes 2 and 3 | `data/local/judge1/judge1.mp4` | drawn by `make judge-video`: 64 s, 720x1280, no sound; not stored | rebuilt on each machine |
| modes 2 and 3 | `data/catalog.json` | 20 items, including the four products of judge1 (default `CATALOG_PATH`) | `0687e99b54c7c6053edb1987af8ca3228f4ff0b53e2588ec1075ce3cdf946ad3` |

## test1 (the recording of the measured paid run)

The test1 recording is a seller showing ten items, recorded on 27 October 2026. It is not in this repository.

Download location: none; the recording is not published.

| Input | File | What it is | sha256 |
|---|---|---|---|
| the video | `data/local/test1/vi-2026-10-27.mp4` | 560,791,445 bytes, 5 min 51 s, HEVC video with AAC sound | `19fc31b71bd7e1c5e41bcecf35c4cbfc8ef3bc8e57df0f08915b363c6666f621` |
| scoring | `data/local/test1/labels.json` | 44,910 bytes, the local price labels the scorer reads (`measure/c5.py`) | `eeeb5a69d9eb790ce366892a3ae8523cc3f4ab699cc62b0636567a2669ac7e65` |
| catalog | `data/comments/c5/catalog.json` | the ten items of the recording (`CATALOG_PATH` for test1) | `fd5e86ad67ada1c0d200386fedd642e688440a34970e795d65b99ebc267adc73` |
| scoring | `data/comments/c5/windows.json` | the item on screen for each part of the recording | `12a1ad920e860a37ddccc375c91f6537f2d7d44ff49c9e89871fb5a38d7c3d51` |
| comments | `data/comments/c5/stream-1.jsonl` | 100 synthetic comments; the measured run used this one | `9c259c030cda0e74a10b6bf725aededdb741d874a3c63ff6653e38c9edb9e79c` |
| comments | `data/comments/c5/stream-2.jsonl` | 100 synthetic comments | `adea20e712298d32650051e224d3e2cf16f376fc42d123bfea423bce270d3f40` |
| comments | `data/comments/c5/stream-3.jsonl` | 100 synthetic comments | `44a6eff0ae7566440d6f20b0029addd09c960d7ad358600a65b8982b8fcf9cc7` |
| comments | `data/comments/c5/stream-4.jsonl` | 100 synthetic comments | `9b2ffc721b40b7b1c3467347ac06455b2ec1807f091c210d664582ae9bc5e95d` |
| comments | `data/comments/c5/stream-5.jsonl` | 100 synthetic comments | `c8fa4c58fdd8adbc1b80074256a1792388d51093436ca20c8b615584faad3feb` |
| customer replies | `data/comments/c5/messenger-1.jsonl` | the simulated customers' Messenger answers for stream 1 | `229578349a9775c94f8522f2879462f00499ac8af89a9472997b2d85ef745fca` |
| customer replies | `data/comments/c5/messenger-2.jsonl` | for stream 2 | `84bf93532427c1ba493a6d6a9d7178973c2071b2c493b0cd85b02eb26a2d9e33` |
| customer replies | `data/comments/c5/messenger-3.jsonl` | for stream 3 | `b2a59f97bc4d6bd3485aae11e80d1c0dd8b79f4076a796c943df5662ab6c245c` |
| customer replies | `data/comments/c5/messenger-4.jsonl` | for stream 4 | `254e160df78f2a5c6d602d26e142f0e76b5efb64d026338819ab4068c929ae78` |
| customer replies | `data/comments/c5/messenger-5.jsonl` | for stream 5 | `e5bd56b6c6eb4778a579f751e1c2456615a9166f833c03cd0ae008e491aea2e2` |

How the streams were written and labelled: `data/comments/c5/README.md`. How to run them against the recording: `docs/RUN.md`, section 6.
