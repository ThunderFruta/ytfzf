# Search YouTube directly instead of using Invidious instance discovery.
scrape="yt"

# Render thumbnails using terminal symbols.
show_thumbnails=1
thumbnail_viewer="chafa"

# Give the thumbnail more terminal cells for finer detail.
fzf_preview_size="80"
# yt-dlp preference used by ytfzf format selection.
ytdl_pref="bestvideo[height=720][fps>=59][fps<=60]+bestaudio/best[height=720][fps>=59][fps<=60]"

# Prefer full-size YouTube thumbnails, with the original search image as fallback.
download_thumbnails() {
    [ "$skip_thumb_download" -eq 1 ] && return 0
    [ -z "$*" ] && return 0
    printf '%s\n' 'Fetching high-resolution thumbnails...'
    curl_config_file="${session_temp_dir}/curl_config"
    : >"$curl_config_file"
    for line in "$@"; do
        id="${line##*;}"
        case "$id" in
            *[!A-Za-z0-9_-]* | '') continue ;;
        esac
        [ "${#id}" -eq 11 ] || continue
        printf 'url="https://i.ytimg.com/vi/%s/maxresdefault.jpg"\noutput="%s/%s.hq.jpg"\n' "$id" "$thumb_dir" "$id" >>"$curl_config_file"
    done
    [ ! -s "$curl_config_file" ] || curl -fsLZ --connect-timeout 3 --max-time 10 -K "$curl_config_file"
    : >"$curl_config_file"
    for line in "$@"; do
        id="${line##*;}"
        if [ -s "$thumb_dir/$id.hq.jpg" ]; then
            mv "$thumb_dir/$id.hq.jpg" "$thumb_dir/$id.jpg"
        else
            printf 'url="%s"\noutput="%s/%s.jpg"\n' "${line%%;*}" "$thumb_dir" "$id" >>"$curl_config_file"
        fi
    done
    [ ! -s "$curl_config_file" ] || curl -fLZ --connect-timeout 3 --max-time 10 -K "$curl_config_file"
}

# Send selected videos through the configured transfer command.
fzf() {
    command fzf "$@"
    menu_status=$?
    case "$menu_status" in
        0) ;;
        1) printf '%s\n' 'ytfzf: No matching video was selected.' >&2 ;;
        130) printf '%s\n' 'ytfzf: Video selection canceled.' >&2 ;;
        *) printf 'ytfzf: Selection menu failed (exit %s).\n' "$menu_status" >&2 ;;
    esac
    return "$menu_status"
}
drive_upload() {
    ytfzf-drive "$@"
}
url_handler="drive_upload"
# Route the video/audio/download shortcuts through the same upload action.
video_player() { drive_upload "$@"; }
audio_player() { drive_upload "$@"; }
downloader() { drive_upload "$@"; }
