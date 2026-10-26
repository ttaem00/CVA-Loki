"""Opt-in scoped discovery by the existing T4 owner over preserved originals.

The map is orientation, not a corpus or a completed-review ledger. Discovery
findings are hypotheses; the same author rereads them before choosing cuts.
"""

def add_active_frames(view, enriched):
    frames = enriched.get("_media_frames", {})
    if frames:
        view["availability"]["pixels"] = "attached sampled frames only; not continuous viewing"
        view["frames"] = [dict(ref=ref, attachment_index=i, source_sec=f["source_sec"],
            dimensions=f["delivered_dimensions"], source_height=f["source_height"], uncertainty=f["uncertainty"])
            for i, (ref, f) in enumerate(frames.items(), 1)]
