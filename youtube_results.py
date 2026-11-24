"""Convert public YouTube page data into ytfzf records (no API key)."""
import json
import re
import sys
from pathlib import Path


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def text(value):
    if isinstance(value, str):
        return value
    value = value or {}
    return value.get('simpleText') or value.get('content') or ''.join(
        run.get('text', '') for run in value.get('runs', []))


def thumbnail(sources):
    sources = [s for s in (sources or []) if s.get('url')]
    url = max(sources, key=lambda s: s.get('width', 0) * s.get('height', 0),
              default={}).get('url', '')
    return 'https:' + url if url.startswith('//') else url


def parse(html, mode):
    match = re.search(r'(?:var\s+ytInitialData\s*=|window\["ytInitialData"\]\s*=)\s*', html)
    if not match:
        raise ValueError('YouTube returned no page data; try the search again.')
    data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    owner = data.get('metadata', {}).get('channelMetadataRenderer', {}).get('title', '')
    results = []
    seen = set()
    for node in walk(data.get('contents', {})):
        row = None
        if 'channelRenderer' in node and mode != 'channel-videos':
            v = node['channelRenderer']
            ident = v.get('channelId', '')
            if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', ident):
                continue
            url = 'https://www.youtube.com/channel/' + ident
            name = text(v.get('title'))
            row = dict(ID=ident, url=url, title='[Channel] ' + name, channel=name,
                       thumbs=thumbnail(v.get('thumbnail', {}).get('thumbnails')),
                       duration=text(v.get('videoCountText')), views='', date='',
                       description=text(v.get('descriptionSnippet')),
                       action='scrape type=youtube-channel search=' + url)
        elif 'playlistRenderer' in node and mode not in ('channel', 'channel-videos'):
            v = node['playlistRenderer']
            ident = v.get('playlistId', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
                continue
            url = 'https://www.youtube.com/playlist?list=' + ident
            images = v.get('thumbnails', [{}])[0].get('thumbnails', [])
            row = dict(ID=ident, url=url, title='[Playlist] ' + text(v.get('title')),
                       channel=text(v.get('longBylineText')), thumbs=thumbnail(images),
                       duration=str(v.get('videoCount', '')) + ' videos', views='', date='',
                       description='', action='scrape type=invidious-playlist search=' + url)
        elif 'videoRenderer' in node and mode != 'channel':
            v = node['videoRenderer']
            row = dict(ID=v.get('videoId', ''), title=text(v.get('title')),
                       channel=owner or text(v.get('longBylineText') or v.get('ownerText')),
                       thumbs=thumbnail(v.get('thumbnail', {}).get('thumbnails')),
                       duration=text(v.get('lengthText')) or 'LIVE',
                       views=text(v.get('shortViewCountText') or v.get('viewCountText')),
                       date=text(v.get('publishedTimeText')),
                       description=text(v.get('descriptionSnippet')))
        elif 'lockupViewModel' in node and mode != 'channel':
            v = node['lockupViewModel']
            if v.get('contentType') != 'LOCKUP_CONTENT_TYPE_VIDEO':
                continue
            meta = v.get('metadata', {}).get('lockupMetadataViewModel', {})
            image = v.get('contentImage', {}).get('thumbnailViewModel', {})
            parts = [part for r in meta.get('metadata', {}).get('contentMetadataViewModel', {}).get('metadataRows', [])
                     for part in r.get('metadataParts', [])]
            labels = [p.get('accessibilityLabel', '') for p in parts]
            duration = next((text(n['thumbnailBadgeViewModel'].get('text'))
                             for n in walk(image.get('overlays', [])) if 'thumbnailBadgeViewModel' in n), '')
            row = dict(ID=v.get('contentId', ''), title=text(meta.get('title')),
                       channel=owner, thumbs=thumbnail(image.get('image', {}).get('sources')),
                       duration=duration, views=next((s for s in labels if 'view' in s), ''),
                       date=next((s for s in labels if 'ago' in s), ''), description='')
        if row is None:
            continue
        ident = row['ID']
        if 'action' not in row:
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', ident):
                continue
            row['url'] = 'https://www.youtube.com/watch?v=' + ident
        if ident not in seen:
            seen.add(ident)
            row['scraper'] = 'youtube_channel_videos' if mode == 'channel-videos' else 'youtube_search'
            results.append(row)
    return results


if __name__ == '__main__':
    try:
        print(json.dumps(parse(Path(sys.argv[1]).read_text(), sys.argv[2]), ensure_ascii=False))
    except (ValueError, OSError) as exc:
        print('ytfzf: ' + str(exc), file=sys.stderr)
        sys.exit(1)
