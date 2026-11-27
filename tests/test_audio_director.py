import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src import audio_director as AD


class AudioDirectorTests(unittest.TestCase):
    def _asset(self, base, name, kind, moods, tags=(), license="by", version="4.0"):
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((name + "licensed audio fixture").encode() * 100)
        return {"path": path.relative_to(base).as_posix(), "kind": kind, "moods": list(moods),
                "tags": list(tags), "title": name, "creator": "Fixture creator", "source": "Fixture",
                "source_url": "https://example.org/original-track", "license": license,
                "license_version": version, "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": f"{name} by Fixture creator, CC BY 4.0",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_scene_audio_uses_story_context_and_explicit_environment_for_sfx(self):
        self.assertEqual("science", AD.music_profile({"category": "science", "setting": "deep space"}))
        self.assertEqual("suspense", AD.music_profile({"emotion": "hushed and mysterious"}))
        self.assertEqual("nature", AD.music_profile({"title": "Forest ecology", "emotion": "calm"}))
        self.assertIsNone(AD.ambience_profile({"emotion": "stormy and tense", "narration": "A tense decision."}))
        self.assertEqual("rain", AD.ambience_profile({"narration": "Rain struck the abandoned station."}))
        self.assertEqual("thunder", AD.ambience_profile({"narration": "Rain fell as thunder rolled nearby."}))
        self.assertTrue(AD.narration_priority({"narration": "The evidence changed everything."}))

    def test_manifest_hash_and_permissive_license_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = self._asset(root, "music/soft.mp3", "music", ["emotional"])
            nc = self._asset(root, "music/nc.mp3", "music", ["emotional"], license="by-nc")
            (root / "manifest.json").write_text(json.dumps({"schema_version": 1,
                                                               "assets": [good, nc]}), encoding="utf-8")
            accepted, diagnostics = AD.load_assets([root])
            self.assertEqual([good["sha256"]], [a["sha256"] for a in accepted])
            self.assertEqual(1, len(diagnostics))
            accepted[0]["path"].write_bytes(b"changed contents")
            reloaded, _ = AD.load_assets([root])
            self.assertEqual([], reloaded)

    def test_plan_keeps_same_score_through_same_emotional_run_and_selects_unique_sfx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            work = Path(tmp) / "checkpoint" / "run"
            library = root / "assets" / "audio"
            library.mkdir(parents=True)
            music = self._asset(library, "music/suspense.mp3", "music", ["suspense"],
                                tags=["cinematic", "instrumental"])
            sfx1 = self._asset(library, "sfx/rain1.mp3", "sfx", ["rain"], tags=["rain", "ambience"])
            sfx2 = self._asset(library, "sfx/rain2.mp3", "sfx", ["rain"], tags=["rain", "ambience"])
            entries = [music, sfx1, sfx2]
            (library / "manifest.json").write_text(json.dumps({"assets": entries}), encoding="utf-8")
            scenes = [{"emotion": "suspenseful", "narration": "Rain falls outside."},
                      {"emotion": "mysterious", "narration": "Rain returns."}]
            director = AD.AudioDirector(root, work, {"online_discovery": False})
            plan = director.plan(scenes, [30, 30])
            self.assertEqual(music["sha256"], plan[0]["music"]["sha256"])
            self.assertEqual(plan[0]["music"]["sha256"], plan[1]["music"]["sha256"])
            self.assertNotEqual(plan[0]["ambience"]["sha256"], plan[1]["ambience"]["sha256"])
            credits = director.credits(plan)
            self.assertEqual(3, len(credits))
            self.assertTrue(all(item["sha256"] for item in credits))

    def test_environment_effect_must_match_the_scene_cue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            library = root / "assets" / "audio"
            library.mkdir(parents=True)
            thunder = self._asset(library, "sfx/thunder.mp3", "sfx", ["rain"], tags=["thunder"])
            (library / "manifest.json").write_text(json.dumps({"assets": [thunder]}), encoding="utf-8")
            director = AD.AudioDirector(root, Path(tmp) / "checkpoint", {"online_discovery": False})
            plan = director.plan([{"narration": "Rain fell as thunder rolled across the valley."}], [12])
            self.assertEqual("thunder", plan[0]["ambience_profile"])
            self.assertEqual(thunder["sha256"], plan[0]["ambience"]["sha256"])

    def test_openverse_is_bounded_to_commercial_compatible_open_licenses(self):
        response = type("Response", (), {"raise_for_status": lambda self: None,
                                         "json": lambda self: {"results": []}})()
        with patch("src.audio_director.requests.get", return_value=response) as get:
            self.assertEqual([], AD._search_openverse("cinematic ambient"))
        params = get.call_args.kwargs["params"]
        self.assertEqual("cc0,by", params["license"])
        self.assertEqual("commercial", params["license_type"])
        self.assertLessEqual(params["page_size"], 20)

    def test_openverse_download_records_license_attribution_and_hash(self):
        payload = b"openly licensed audio bytes" * 400

        class Download:
            url = "https://cdn.example.org/audio.mp3"
            headers = {"Content-Type": "audio/mpeg", "Content-Length": str(len(payload))}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def raise_for_status(self):
                return None
            def iter_content(self, chunk_size):
                yield payload

        row = {"id": "f492efed-b0c3-4fbe-9a62-7a1f", "filetype": "mp3",
               "url": "https://cdn.example.org/audio.mp3", "foreign_landing_url": "https://source.example.org/item",
               "license": "by", "license_version": "4.0",
               "license_url": "https://creativecommons.org/licenses/by/4.0/",
               "title": "Ambient score", "creator": "Sound artist", "source": "Example source",
               "attribution": "Ambient score by Sound artist, CC BY 4.0", "tags": [{"name": "suspense"}]}
        with tempfile.TemporaryDirectory() as tmp, patch("src.audio_director.requests.get", return_value=Download()):
            asset = AD._download_openverse(row, tmp, "music", "suspense")
            self.assertIsNotNone(asset)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), asset["sha256"])
            manifest = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("Sound artist", manifest["assets"][0]["creator"])
            self.assertEqual("by", manifest["assets"][0]["license"])

    def test_online_discovery_has_bounded_search_and_candidate_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            director = AD.AudioDirector(root, root / "checkpoint" / "run",
                                        {"online_discovery": True, "max_discoveries_per_run": 1,
                                         "discovery_timeout_seconds": 8})
            rows = [{"id": str(index)} for index in range(8)]
            with patch("src.audio_director._search_openverse", return_value=rows) as search, \
                 patch("src.audio_director._download_openverse", return_value=None) as download:
                director.plan([{"narration": "Rain fell along the street.", "emotion": "mysterious"}], [10])
            self.assertEqual(1, search.call_count)
            self.assertEqual(2, download.call_count)

    def test_online_discovery_has_bounded_search_and_candidate_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = AD.AudioDirector(root, root / "checkpoint" / "run",
                                     {"online_discovery": True, "max_discoveries_per_run": 1,
                                      "discovery_timeout_seconds": 8})
            rows = [{"id": str(i)} for i in range(8)]
            with patch("src.audio_director._search_openverse", return_value=rows) as search, \
                 patch("src.audio_director._download_openverse", return_value=None) as download:
                audio.plan([{"narration": "Rain fell along the street.", "emotion": "mysterious"}], [10])
            self.assertEqual(1, search.call_count)
            self.assertEqual(2, download.call_count)


if __name__ == "__main__":
    unittest.main()
