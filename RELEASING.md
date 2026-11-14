# Releasing Ultimate Downloader

The release title is **v7.2 - Châu Thành Donor Downloads & Episode Orders**. Use the [v7.2 changelog section](CHANGELOG.md#v72) as the GitHub release body.

## Prepare and verify

Check that the README and `APP_VERSION` in `ultimate_downloader.py` agree. The app heading, About dialog, and saved-session metadata all read that version. The archive snapshot must match the downloader from the v7.1 tag. Use Python 3.10 or later and Node.js for the queue interaction checks.

```powershell
python -m pip install anywidget ipywidgets requests yt-dlp
python -m py_compile ultimate_downloader.py
python -X utf8 -m unittest test_startup test_tmdb_anime_classification test_tmdb_episode_orders test_queue_editing test_queue_additions_torbox test_queue_table test_progress_panel test_live_queue test_pikpak test_download_backoff test_chauthanh test_torbox_jit test_drive_cancellation test_drive_settings test_okru
git diff --check -- . ':(exclude)archive/**'
git hash-object archive/ultimate_downloader_v7.1.py
git rev-parse v7.1:ultimate_downloader.py
```

Compare the two hashes. The archive keeps the original released source, including its historical whitespace. Run the named test modules so local exploratory files stay outside the release checks.

## Colab smoke test

Use the candidate script in a fresh Colab notebook. The automated tests exercise local logic with mocked provider responses.

- Check the v7.2 heading and About dialog. Run a small direct-link batch with Drive API uploads enabled, add a link during the batch, and use Stop, Resume and Retry Failed. Check final Drive files and settings after restarting the runtime.
- Use a Châu Thành supporter session through **Settings → PC session → Use PC login** or Colab Secret `CHAUTHANH_PHPSESSID`. Try three eligible donor files with **Parallel DLs** set to at least three, interrupt and resume a transfer, and confirm file size and completion in Drive. Check that an expired session prompts for a fresh PC value. A previous live batch completed using the earlier two-file allocation. The three-file configuration and live runtime-browser login still need testing.
- Apply an alternate TMDB episode order to selected absolute-numbered files and matching subtitles. Check the queue preview, explicit season markers, an omitted episode, and the saved order after Resume.
- Check a small PikPak share and a TorBox WebDL link with configured accounts. Verify that the selected PikPak file reaches Drive and TorBox recognises the completed fetch. Try a short YouTube video with subtitles and a naming edit.

## Publish

Run these steps from the reviewed v7.2 candidate after confirming that it can fast-forward the remote main branch. Check that the remote tag query returns no v7.2 tag before pushing.

```powershell
git fetch origin --tags
git log --oneline --left-right HEAD...origin/main
git ls-remote --tags origin refs/tags/v7.2
git push origin HEAD:main
git tag -a v7.2 -m "v7.2: Châu Thành donor downloads and episode orders"
git push origin refs/tags/v7.2
```

Create the release from the tagged commit using the exact changelog text.

```powershell
$changelog = Get-Content -LiteralPath CHANGELOG.md -Raw -Encoding UTF8
$notes = [regex]::Match($changelog, '(?ms)^## v7\.2\r?\n(.*?)^---[ \t]*\r?$').Groups[1].Value.Trim()
if (-not $notes) { throw 'v7.2 release notes were not found' }
$releaseBodyPath = Join-Path ([System.IO.Path]::GetTempPath()) 'ultimate-downloader-v7.2-release.md'
[System.IO.File]::WriteAllText($releaseBodyPath, $notes, [System.Text.UTF8Encoding]::new($false))
gh release create v7.2 --verify-tag --title "v7.2 - Châu Thành Donor Downloads & Episode Orders" --notes-file $releaseBodyPath
```

Verify the [GitHub release](https://github.com/xersbtt/ultimate-downloader-colab/releases) and the downloader script at the tag. Replace `main` with `v7.2` in the README launcher to run the tagged script.
