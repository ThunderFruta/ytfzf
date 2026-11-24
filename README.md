# ytfzf transfers

A companion for [ytfzf](https://github.com/pystardust/ytfzf) that turns selected YouTube videos into 720p Matroska sections of up to 30 minutes. It preserves the source frame rate, normalizes audio, and saves a resumable job under `~/Videos/ytfzf-drive`. Each section is verified remotely by size, content checksum, and asset or file ID before its local video is removed.

## Install

Install ytfzf, Python 3.9+, yt-dlp, ffmpeg and ffprobe. Install rclone for Drive, `gh` for GitHub, and Chafa for terminal thumbnails. Put `~/.local/bin` on your PATH.

```sh
install -d ~/.local/bin ~/.local/share/ytfzf-install ~/.config/ytfzf-drive
install -m 755 ytfzf-drive ~/.local/bin/
install -m 644 range_stream.py audio_normalize.py youtube_results.py ~/.local/share/ytfzf-install/
install -m 644 config/audio.json ~/.config/ytfzf-drive/audio.json
```

Use the sample [ytfzf configuration](config/conf.sh) and [scrapers](config/scrapers) in `~/.config/ytfzf/`, or add `url_handler="ytfzf-drive"` to an existing ytfzf configuration.

## Destinations

Set `YTFZF_DRIVE_FOLDER_ID` to the destination folder ID and run `ytfzf-drive --setup` for Drive. GitHub uses the current `gh auth login` account, which needs write access to an initialized repository with a default branch. Choose a repository whose release visibility suits the videos you intend to publish.

Set defaults in `~/.config/ytfzf-drive/destinations.json`:

```json
{
  "destination": "auto",
  "github_repo": "your-account/your-repository"
}
```

- `auto` checks Drive first. If the Drive check fails before a job starts and a GitHub repository is configured, the job uses GitHub.
- `drive` requires the configured Drive folder.
- `github` uses GitHub Releases. Video sections are public when the repository is public. Each asset must be smaller than 2 GiB.

```sh
ytfzf-drive --destination github --github-repo your-account/your-repository VIDEO_URL
ytfzf-drive --resume ~/Videos/ytfzf-drive/selection-EXAMPLE
ytfzf-drive --retry /path/to/retained-section.mkv
```

Jobs retain their destination when resumed. A new destination setting applies to new jobs. Use a repository you control and publish only videos you have permission to distribute.
