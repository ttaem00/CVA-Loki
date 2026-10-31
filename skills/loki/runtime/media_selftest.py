"""Check installed local media extraction and report image custody with a fixed fixture."""
from __future__ import annotations

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import uuid

if __package__:
    from .selftest import original_records
else:
    from selftest import original_records

KIND = "synthetic_media_contract_selftest"
SEMANTIC_AUTHOR = "synthetic_fixture_not_a_model"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def media_policy(policy):
    """Select only installed media tools for this check, without loading a model."""
    value = dict(policy)
    value.update(features="media", multimodal_runtime=None)
    return value


def checked_image(metadata, selected):
    """Require the exact delivered frame bytes, hash, clock and reference in a report."""
    packet = metadata["editorial_review_packet"]
    image = packet["editorial_highlights"][0]["image"]
    data = base64.b64decode(image["data_url"].split(",", 1)[1], validate=True)
    original = Path(selected["image"]["path"]).read_bytes()
    require(data == original, "media_selftest_report_pixels_changed")
    require(hashlib.sha256(data).hexdigest() == image["sha256"] == selected["image"]["sha256"],
            "media_selftest_report_image_hash_changed")
    require(image["frame_ref"] == selected["frame_ref"]
            and image["source_sec"] == selected["source_sec"]
            and image["time_binding"] == selected["time_binding"], "media_selftest_report_clock_changed")
    require(packet["broadcast_map"]["d2"][0]["image"] == image,
            "media_selftest_timeline_image_changed")
    return image


def checked_html_image(html, image, rendered_data_url):
    """Bind the actual visible image to the selected frame and renderer's WebP output."""
    class Images(HTMLParser):
        def __init__(self):
            super().__init__()
            self.selected = False
            self.sources = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "figure":
                self.selected = (attrs.get("data-frame-ref") == image["frame_ref"]
                                 and attrs.get("data-image-sha256") == image["sha256"])
            elif tag == "img" and self.selected:
                self.sources.append(attrs.get("src"))

        def handle_endtag(self, tag):
            if tag == "figure":
                self.selected = False

    parsed = Images()
    parsed.feed(html)
    require(rendered_data_url in parsed.sources, "media_selftest_html_image_missing")
    require(rendered_data_url.startswith("data:image/webp;base64,"), "media_selftest_html_image_format")
    return hashlib.sha256(base64.b64decode(rendered_data_url.split(",", 1)[1], validate=True)).hexdigest()


def response(prepared, selected):
    image = dict(frame_ref=selected["frame_ref"], alt="Synthetic test frame", caption="Fixed media fixture")
    return {"base_revision": prepared["base_revision"],
        "summary": "A later quiet reply changes the initial claim.", "summary_evidence_refs": ["e1", "e3"],
        "ranges": [
            dict(id="d1", level="D1", title="Conversation", content="A claim and later reply",
                 start="00:00:00", end="00:00:20", evidence_refs=["e1", "e3"]),
            dict(id="d2", level="D2", parent_id="d1", title="Reply", content="The later reply",
                 start="00:00:09", end="00:00:20", evidence_refs=["e3"], image=image)],
        "points": [dict(id="p", title="Quiet reply", content="A contradiction", timestamp="00:00:10",
                        d2_ref="d2", start_evidence_ref="e3", evidence_refs=["e3"])],
        "stories": [dict(story_ref="s", point_ref="p", title="Quiet reply", why_notable="The claim changes",
                         used_evidence_ids=["e3"], roles=dict(setup=[], performance=[], event=["e3"], reaction=[], result=[]))],
        "proposals": [dict(story_refs=["s"], point_refs=["p"], title="Reply", story_summary="quiet rebuttal",
            selection_reason="understand the changed claim", image=image,
            source_spans=[dict(start_sec=10, end_sec=12, story_roles=["conversation"], evidence_refs=["e3"], reason="actual reply")],
            story_beats=[dict(role="core_event", span_indexes=[0], evidence_refs=["e3"], reason="reply")],
            estimated_edited_duration=dict(min_sec=2, max_sec=2, category="short"), missing_information=[], uncertainties=[])],
        "group_outcomes": [dict(terminal_state="represented", flow_refs=["broadcast"], range_refs=["d1", "d2"],
            reason="The conversation retains the initial claim and later quiet rebuttal.", evidence_refs=["e1", "e3"])],
        "unresolved_questions": []}


def run(runtime, workspace):
    runtime, root = Path(runtime).resolve(strict=True), Path(workspace).resolve(strict=True)
    original_config = root / "loki.json"
    config_bytes = original_config.read_bytes()
    policy = json.loads(config_bytes)
    require(policy.get("features") in {"media", "multimodal"}, "media_selftest_media_not_installed")
    tools = json.loads((root / ".loki-tools.json").read_text(encoding="utf-8-sig"))
    ffmpeg = Path(tools["tools"]["ffmpeg"]["path"]).resolve(strict=True)
    require(sha(ffmpeg) == tools["tools"]["ffmpeg"]["sha256"], "media_selftest_ffmpeg_changed")
    token = uuid.uuid4().hex
    video = str(800000000000000 + int(token[:12], 16))
    proof = root / "verification" / ("media-" + token)
    source = root / policy["work_dir"] / video

    for path in (proof, source):
        require(path.resolve().is_relative_to(root), "media_selftest_path_outside_workspace")
        current = root
        for part in path.relative_to(root).parts:
            current /= part
            require(not current.is_symlink() and not (current.exists() and
                    getattr(current.stat(), "st_file_attributes", 0) & 0x400), "media_selftest_link_forbidden")
    proof.mkdir(parents=True)
    source.mkdir(parents=True)
    config = original_config
    if policy["features"] == "multimodal":


        config = root / ("loki-media-selftest-" + token + ".json")
        with config.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(media_policy(policy), stream, ensure_ascii=False, indent=2)

    def write(name, value):
        path = proof / name
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        return str(path)

    def call(*args):
        value = subprocess.run([sys.executable, "-B", str(runtime / "loki.py"), "--config", str(config), *args],
            cwd=runtime, text=True, encoding="utf-8", capture_output=True, timeout=180)
        require(value.returncode == 0, "media_selftest_cli_failed: " + args[0] + ": " + value.stdout + value.stderr)
        return json.loads(value.stdout)

    media = source / "fixture.mp4"
    generated = subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "20", "-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p", "-c:a", "aac", str(media)],
        text=True, encoding="utf-8", capture_output=True, timeout=120)
    require(generated.returncode == 0 and media.is_file(), "media_selftest_fixture_failed: " + generated.stderr)
    srt, base = source / "source.srt", proof / "base.md"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\ninitial claim\n\n2\n00:00:05,000 --> 00:00:06,000\nunrelated schedule\n\n3\n00:00:10,000 --> 00:00:12,000\nquiet rebuttal\n", encoding="utf-8")
    base.write_text("Existing fixture base; preserve this.\n", encoding="utf-8")
    preserved = {str(path): sha(path) for path in (srt, base, media)}
    spec = dict(vod_info=dict(video_no=video, title="Synthetic media conversation", channel_id="", channel_name="Synthetic",
        duration=20, publish_date="2026-01-01"), source_work_dir=str(source), srt_path=str(srt), media_path=str(media),
        base_path=str(base), job_id="synthetic-media-" + token,
        author=dict(thread_id="synthetic-media-fixture", turn_id="fixture-no-model-call", actual_model="unknown"))
    prepared = call("prepare", "--spec", write("spec.json", spec))
    identity = ["--job-root", prepared["job_root"], "--expected-revision", prepared["prepared_revision"]]
    envelope = dict(schema_version="ew.tool.v1", source_id=prepared["source_id"], world_revision=prepared["world_revision"],
                    question_id="synthetic-media-originals")
    action = dict(envelope, action="read_evidence", args=dict(intervals_sec=[[0, 20]], families=["stt"], context_cues=0, max_bytes=16000))
    originals = call("read", *identity, "--action", write("original-action.json", action))
    require(isinstance(originals, dict), "media_selftest_originals_incomplete")
    records = original_records(originals)
    require(originals.get("original_loaded") is True and originals.get("status") == "complete"
            and originals.get("next_cursor") is None and len(records) == 3
            and all(row.get("fragment_complete") is True for row in records), "media_selftest_originals_incomplete")
    require(originals.get("source_id") == prepared["source_id"]
            and originals.get("world_revision") == prepared["world_revision"]
            and isinstance(originals.get("read_id"), str) and bool(originals["read_id"]),
            "media_selftest_original_identity_mismatch")
    require([row.get("raw_text") for row in records] == ["initial claim", "unrelated schedule", "quiet rebuttal"],
            "media_selftest_original_text_mismatch")
    require(all(isinstance(row.get("record_id"), str) and len(row["record_id"]) == 64
                and all(char in "0123456789abcdef" for char in row["record_id"]) for row in records),
            "media_selftest_original_record_identity_invalid")
    original_path = write("originals.json", originals)
    action = dict(envelope, action="read_frames", args=dict(start_sec=10, duration_sec=2, inspection_mode="scene"))
    pages, frames = [], []
    for index in range(3):
        page = call("read", *identity, "--action", write("frame-action-" + str(index) + ".json", action))
        require(len(page["frames"]) == 3 and page["pixels_seen"] == "UNVERIFIED", "media_selftest_frame_page_invalid")
        pages.append(page)
        frames.extend(page["frames"])
        write("frame-page-" + str(index) + ".json", page)
        if index < 2:
            require(bool(page.get("next_cursor")), "media_selftest_frame_cursor_missing")
            action = dict(envelope, action="read_frame_page", args=dict(cursor=page["next_cursor"]))
    require(len({frame["frame_ref"] for frame in frames}) == 9 and not pages[-1].get("next_cursor"),
            "media_selftest_frame_count_invalid")
    require(all(Path(frame["image"]["path"]).suffix.lower() in {".jpg", ".jpeg"}
                and sha(frame["image"]["path"]) == frame["image"]["sha256"]
                and 10 <= frame["source_sec"] <= 12 for frame in frames), "media_selftest_frame_custody_invalid")
    selected = frames[4]
    submitted = call("submit", *identity, "--response", write("response.json", response(prepared, selected)))
    require(submitted["external_transport_calls"] == 0 and not submitted["published"] and not submitted["manager_saved"],
            "media_selftest_authority_changed")
    paths = submitted["paths"]
    require(all(Path(paths[k]).is_file() for k in ("html", "markdown", "metadata")), "media_selftest_report_missing")
    metadata = json.loads(Path(paths["metadata"]).read_text(encoding="utf-8-sig"))
    image = checked_image(metadata, selected)
    from pipeline.editorial_highlight_cards import editorial_scene_image_data_url
    html_image_hash = checked_html_image(Path(paths["html"]).read_text(encoding="utf-8"), image,
                                        editorial_scene_image_data_url(image["data_url"]))
    authored = json.loads((Path(prepared["job_root"]) / "result.json").read_text(encoding="utf-8-sig"))["full_author_receipt"]
    require(authored["pixels_seen"] == "UNVERIFIED" and authored["external_transport_calls"] == 0,
            "media_selftest_pixel_interpretation_claimed")
    require(original_config.read_bytes() == config_bytes and all(sha(path) == value for path, value in preserved.items()),
            "media_selftest_original_changed")
    result = dict(status="PASS", kind=KIND, semantic_author=SEMANTIC_AUTHOR, pixels_interpreted="not_claimed",
        editorial_quality="not_evaluated", model_inference_calls=0, external_transport_calls=0, workspace=str(root),
        verification_directory=str(proof), verification_config=str(config), verification_profile="media_only",
        report_paths=paths, frame_path=selected["image"]["path"],
        frame_ref=selected["frame_ref"], frame_sha256=image["sha256"], time_binding=image["time_binding"],
        html_image_sha256=html_image_hash, html_image_format="webp",
        frame_count=len(frames), frame_page_count=len(pages),
        frame_read_pages=[str(proof / ("frame-page-" + str(i) + ".json")) for i in range(3)],
        original_read=dict(path=original_path, read_id=originals["read_id"], source_id=originals["source_id"],
                           world_revision=originals["world_revision"], complete_record_count=len(records)),
        source_hashes=preserved, checks=["real synthetic MP4 with audio", "nine JPEGs in three pages", "original STT read",
        "selected report data URL bytes/hash/clock", "normal HTML Markdown metadata", "source base config preserved"])
    write("media-selftest-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Existing media/full installed workspace")
    args = parser.parse_args(argv)
    try:
        result = run(Path(__file__).resolve().parent, args.workspace)
    except (ValueError, OSError, RuntimeError, KeyError, AssertionError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(status="FAIL", kind=KIND, semantic_author=SEMANTIC_AUTHOR, error=str(error)), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
