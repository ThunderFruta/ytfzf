"""Measured, repeatable loudness normalization with atomic media replacement."""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

SETTINGS = Path.home() / '.config/ytfzf-drive/audio.json'


def local_run(command, capture=False):
    return subprocess.run(command, text=True, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE, check=True)


def settings():
    config = json.loads(SETTINGS.read_text())
    for key, low, high in [('integrated_lufs', -30, -10), ('true_peak_dbtp', -8, -1),
                           ('loudness_range_lu', 1, 20)]:
        if not low <= float(config[key]) <= high:
            raise ValueError('Invalid audio setting: ' + key)
    if config['aac_bitrate'] not in ('128k', '160k', '192k', '256k', '320k'):
        raise ValueError('Invalid AAC bitrate')
    return config


def md5(path):
    digest = hashlib.md5()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def probe(path, run=local_run):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                           '-of', 'json', str(path)], capture=True).stdout)


def base_filter(config):
    # Half a dB of encoder headroom keeps AAC reconstruction under the ceiling.
    return (f"loudnorm=I={config['integrated_lufs']}:"
            f"TP={float(config['true_peak_dbtp']) - 0.5}:LRA={config['loudness_range_lu']}")


def measure(path, config, run=local_run):
    result = run(['ffmpeg', '-hide_banner', '-nostdin', '-threads', '1', '-i', str(path),
                  '-map', '0:a:0', '-vn', '-sn', '-dn', '-af', base_filter(config) + ':print_format=json',
                  '-f', 'null', '-'], capture=True)
    matches = re.findall(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not matches:
        raise RuntimeError('FFmpeg returned no loudness measurements')
    return json.loads(matches[-1])


def receipt_path(path):
    return Path(str(path) + '.volume.json')


def verify_loudness(path, run=local_run):
    """Measure encoded output without also calculating another normalized signal."""
    result = run(['ffmpeg', '-hide_banner', '-nostdin', '-threads', '1', '-i', str(path),
                  '-map', '0:a:0', '-vn', '-sn', '-dn', '-af', 'ebur128=peak=true:framelog=verbose',
                  '-f', 'null', '-'], capture=True)
    summary = result.stderr.rsplit('Summary:', 1)[-1]
    values = {}
    for key, pattern in [('input_i', r'\bI:\s*(-?[\d.]+|[-+]?inf)\s+LUFS'),
                         ('input_tp', r'Peak:\s*(-?[\d.]+|[-+]?inf)\s+dBFS'),
                         ('input_lra', r'LRA:\s*([\d.]+)\s+LU'),
                         ('input_thresh', r'Threshold:\s*(-?[\d.]+)\s+LUFS')]:
        match = re.search(pattern, summary)
        if not match:
            raise RuntimeError('No output loudness measurement: ' + key)
        values[key] = match[1]
    return values


def is_current(path, config=None):
    path = Path(path)
    receipt = receipt_path(path)
    if not receipt.exists() or not path.is_file():
        return False
    record = json.loads(receipt.read_text())
    return (record.get('settings') == (config or settings())
            and record.get('size') == path.stat().st_size
            and record.get('md5') == md5(path))


def normalize(path, run=local_run):
    path = Path(path).resolve(strict=True)
    config = settings()
    if is_current(path, config):
        return json.loads(receipt_path(path).read_text())
    before = probe(path, run)
    audio = [s for s in before['streams'] if s['codec_type'] == 'audio']
    if len(audio) != 1:
        raise RuntimeError(f'Expected one audio stream, found {len(audio)}: {path}')
    original_stat = path.stat()
    measured = measure(path, config, run)
    if not math.isfinite(float(measured['input_i'])):
        raise RuntimeError('Audio is silent and cannot be normalized: ' + str(path))
    adjustment = base_filter(config) + ''.join(
        f':{target}={measured[source]}' for target, source in
        [('measured_I', 'input_i'), ('measured_TP', 'input_tp'),
         ('measured_LRA', 'input_lra'), ('measured_thresh', 'input_thresh'), ('offset', 'target_offset')])
    adjustment += ':linear=true:print_format=json'
    descriptor, name = tempfile.mkstemp(prefix='.volume-', suffix=path.suffix, dir=path.parent)
    import os
    os.close(descriptor)
    staging = Path(name)
    try:
        correction = 0.0
        limiter_db = config['true_peak_dbtp'] - 1.0
        for attempt in range(4):
            filters = adjustment
            if attempt:
                filters += (f',volume={correction}dB,alimiter=limit={10 ** (limiter_db / 20)}'
                            ':level=false:latency=true')
            run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-threads', '1', '-filter_threads', '1',
                 '-i', str(path), '-map', '0', '-map_metadata', '0', '-c', 'copy',
                 '-c:a:0', 'aac', '-b:a:0', config['aac_bitrate'], '-ar:a:0', '48000',
                 '-filter:a:0', filters, str(staging)], capture=True)
            verified = verify_loudness(staging, run)
            error = config['integrated_lufs'] - float(verified['input_i'])
            peak_excess = float(verified['input_tp']) - config['true_peak_dbtp']
            if abs(error) <= 0.5 and peak_excess <= 0:
                break
            correction += error
            if peak_excess > 0:
                limiter_db -= peak_excess + 0.25
        else:
            raise RuntimeError('Audio could not meet loudness and peak targets: ' + str(verified))
        after = probe(staging, run)
        def video_signature(metadata):
            return [(s.get('codec_name'), s.get('width'), s.get('height'), s.get('avg_frame_rate'))
                    for s in metadata['streams'] if s['codec_type'] == 'video']
        if video_signature(before) != video_signature(after):
            raise RuntimeError('Video properties changed during audio normalization')
        if abs(float(before['format']['duration']) - float(after['format']['duration'])) > 0.25:
            raise RuntimeError('Media duration changed during audio normalization')
        if abs(float(verified['input_i']) - config['integrated_lufs']) > 0.5:
            raise RuntimeError('Normalized loudness missed the target: ' + str(verified))
        if float(verified['input_tp']) > config['true_peak_dbtp']:
            raise RuntimeError('Normalized audio exceeded the true-peak ceiling: ' + str(verified))
        if (path.stat().st_size, path.stat().st_mtime_ns) != (original_stat.st_size, original_stat.st_mtime_ns):
            raise RuntimeError('Source changed during normalization; replacement stopped')
        record = dict(settings=config, before=measured, after=verified,
                      size=staging.stat().st_size, md5=md5(staging))
        staging.chmod(original_stat.st_mode & 0o777)
        staging.replace(path)
        sidecar = receipt_path(path)
        temporary = sidecar.with_name(sidecar.name + '.tmp')
        temporary.write_text(json.dumps(record, indent=2) + '\n')
        temporary.replace(sidecar)
        return record
    finally:
        staging.unlink(missing_ok=True)
