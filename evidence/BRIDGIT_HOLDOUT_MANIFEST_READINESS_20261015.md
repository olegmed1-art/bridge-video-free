# Local adjacent-frame holdout readiness

On 2026-10-15, the known Gold-v2 Library ID
`libfile_1894d96a69848191b618c2681d1475b8` and the eight sprite IDs
listed in `CONNECTED_SOURCE_DISCOVERY_20261014.md` were prepared by the
authorized Library route. It returned nine signed transfers, no local
`workspace_path`. The current Library file-transfer helper was given the
complete Gold transfer and an absolute destination in this checkout. It
returned `library file transfer failed: download failed`. The other eight
transfers were not retried. No reference bytes, hashes, or ZIP contents were
verified. The previous consumer-local attempt on 2026-10-14 failed at the
hosted-apps DNS stage. Neither result is evidence that a sprite matches its
pinned hash.

`bridge_vision/bridgit_holdout_manifest.py` now checks the structure of a
two-frame local manifest: exact file SHA-256, PNG signature and dimensions,
distinct frame bytes, increasing PTS claims, adjacent observation indices,
two matching `READ` board-number observations per frame, and matching
source-video hash, episode ID, board epoch, and board number across both
frames. It resolves frame paths under the supplied root and rejects absent or
escaping files. Its success state is `STRUCTURAL_PASS`; it expressly reports
`ocr_accuracy_evaluated=false` and `source_pts_verified_by_decode=false`.
Actual PTS and common-board provenance still require the source video and a
verified extraction receipt.

The existing owner-provided Diana73 frame is 1680x934, 1,766,358 bytes,
SHA-256 `b7a960a7bfcf3e3cbc4dc191988baa2d31ec740d72fb58e2a9c61f77ef103c13`.
A local test reads these bytes and confirms they match the stored receipt,
then proves the one-frame evidence remains `BLOCKED` for a two-frame holdout.
The remaining positive and boundary cases use tiny generated PNGs; they do
not measure recognition accuracy.

Run: `..\.venv-geometry\Scripts\python.exe -m pytest
tests/test_bridge_vision_holdout_manifest.py
tests/test_bridge_vision_adjacent_identity_negative.py
tests/test_bridge_vision_bridgit_board_number.py
tests/test_bridge_vision_bridgit_primary_video.py
tests/test_bridge_vision_multiframe.py -q --basetemp=.tmp/pytest-holdout-20261015`.
The recognition holdout remains unrun pending reference bytes and a second
independent, provenance-verified frame of the same board.
