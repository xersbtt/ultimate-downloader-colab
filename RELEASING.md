# Releasing Ultimate Downloader

The current release target is **v7.1 — PikPak & Live Download Reliability**. The Drive renamer is an experimental standalone tool included alongside the downloader. This checklist prepares the release; pushing and publishing are separate steps. The v7.1 section of [CHANGELOG.md](CHANGELOG.md#v71) is the release body.

## Prepare and verify

1. Check the README, changelog, app heading, About dialog and saved-session version all describe v7.1.
2. Keep the previous release's script in `archive/ultimate_downloader_v7.0.py`, copied from the `v7.0` tag. Include the experimental standalone renamer and all maintained regression tests in the release commit, and preserve its experimental label in the documentation.
3. Run the local checks below from the repository root. Use Python 3.10 or later and Node.js for the queue interaction check. Local test dependencies are `anywidget`, `ipywidgets`, `requests` and `yt-dlp`; Colab installs runtime tools when needed.

```powershell
python -m pip install anywidget ipywidgets requests yt-dlp
python -m py_compile ultimate_downloader.py rename_existing_files.py
python -m unittest test_tmdb_anime_classification test_queue_editing test_queue_additions_torbox test_queue_table test_progress_panel test_live_queue test_pikpak test_download_backoff test_rename_existing_files test_drive_settings test_okru
git diff --check -- . ':(exclude)archive/**'
```

The named modules run regression checks without live service credentials. Avoid blanket test discovery: other local `test_*.py` files include exploratory checks and are intentionally ignored. Review skipped tests; missing `anywidget`, Node.js or `yt-dlp` reduces coverage.

Archived scripts preserve the original released source, including historical whitespace. Exclude `archive/` from whitespace checks and verify each new snapshot against its release tag.

## Colab smoke test

Use the prepared scripts in a fresh Colab notebook before publishing. Local tests use mocks and do not verify live provider responses, Google consent or Drive transfers.

- Confirm the heading and About dialog show v7.1 and saved Drive API preferences survive a runtime restart. Start a reviewed batch with API uploads enabled and check Google consent and the final Drive file.
- Resolve a small direct-link batch, add another link while it runs, stop it, then Resume. Check completed files are retained and the final toolbar and Retry Failed control recover correctly.
- Resolve a small PikPak share with a token from Secrets, select one file and confirm only that file is saved and transferred. Check an owned file and a nested folder; retry or resume an interrupted transfer.
- Download a short YouTube video with a queue naming edit and subtitles. Check per-video progress, the final video name and matching subtitle names. Check an OK.ru link if available.

Check the experimental renamer separately in its own notebook: preview a small test folder containing a video and subtitle, verify destinations, move selected files, and try both numbering conversions with a known TMDB match. Record its limitations separately from the downloader smoke test.

## Commit, push and publish

After the checks pass, review the changes and commit the intended release files. The explicit list includes the previously ignored Drive settings and OK.ru regression tests.

```powershell
git status --short
git diff --stat
git add .gitignore README.md CHANGELOG.md RELEASING.md ultimate_downloader.py rename_existing_files.py archive/ultimate_downloader_v7.0.py test_tmdb_anime_classification.py test_queue_editing.py test_queue_additions_torbox.py test_queue_table.py test_progress_panel.py test_live_queue.py test_pikpak.py test_download_backoff.py test_rename_existing_files.py test_drive_settings.py test_okru.py
git diff --cached --check -- . ':(exclude)archive/**'
git diff --cached --stat
git commit -m "v7.1: PikPak and live download reliability"
```

Before pushing, fetch the remote and check for divergence. If `origin/main` has new commits, integrate them and rerun affected checks. Ensure `v7.1` does not already exist locally or remotely.

```powershell
git fetch origin --tags
git log --oneline --left-right HEAD...origin/main
git tag --list v7.1
git ls-remote --tags origin refs/tags/v7.1
git push origin main
git tag -a v7.1 -m "v7.1: PikPak and live download reliability"
git push origin v7.1
```

Run the push commands from `main` after resolving any divergence. Create the GitHub release with the exact changelog section; the temporary body file preserves its formatting.

```powershell
$changelog = Get-Content -LiteralPath CHANGELOG.md -Raw
$notes = [regex]::Match($changelog, '(?ms)^## v7\.1\r?\n(.*?)^---\s*$').Groups[1].Value.Trim()
if (-not $notes) { throw 'v7.1 release notes were not found' }
$releaseBodyPath = Join-Path ([System.IO.Path]::GetTempPath()) 'ultimate-downloader-v7.1-release.md'
[System.IO.File]::WriteAllText($releaseBodyPath, $notes, [System.Text.UTF8Encoding]::new($false))
gh release create v7.1 --verify-tag --title "v7.1 - PikPak & Live Download Reliability" --notes-file $releaseBodyPath
```

Finally, verify the [GitHub release](https://github.com/xersbtt/ultimate-downloader-colab/releases), its tag, and both scripts at the tag. The README's `main` commands follow the latest source; replace `main` with `v7.1` to use the published release.
