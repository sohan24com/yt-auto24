# Audio library and license manifest

The editor works offline with this library and checksummed assets cached from earlier runs. It never assumes an
unregistered file is licensed. Put music in `music/` and environmental recordings in `sfx/` under this directory.

Create `manifest.json` with one entry per file:

```json
{
  "schema_version": 1,
  "assets": [
    {
      "path": "music/example.mp3",
      "kind": "music",
      "profile": "suspense",
      "moods": ["suspense", "cinematic"],
      "tags": ["instrumental", "ambient"],
      "title": "Example score",
      "creator": "Creator name",
      "source": "Source collection",
      "source_url": "https://example.org/track",
      "license": "by",
      "license_version": "4.0",
      "license_url": "https://creativecommons.org/licenses/by/4.0/",
      "attribution": "Example score by Creator, CC BY 4.0, source URL",
      "sha256": "lowercase SHA-256 of the exact audio file"
    }
  ]
}
```

The editor currently accepts only CC0 1.0 or CC BY 2.0/2.5/3.0/4.0 entries. It excludes NC (noncommercial), ND
(no derivatives), and SA (share-alike) licenses because documentary output may be monetized and the soundtrack is
edited/mixed. Confirm the license on the original source page before adding a track. Openverse is a discovery index,
not a legal guarantee that every indexed creator's license assertion is correct. Downloaded assets retain source,
creator, attribution, license URL, file hash and API metadata in their manifest and each run's `audio_report.json`.

Online discovery uses Openverse's anonymous, rate-limited API and makes at most
`audio.max_discoveries_per_run` searches/downloads within `audio.discovery_timeout_seconds` (at most two candidates
per search). It is optional and can be
disabled with `audio.online_discovery: false`. No API key or paid service is needed.
