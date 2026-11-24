"""Feed ffmpeg selected YouTube streams through bounded upstream byte requests."""
import re
import sys
import multiprocessing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit

CHUNK_BYTES = 10 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Every upstream request remains on the validated HTTPS media host.
urlopen = build_opener(NoRedirect()).open


class RangeStream:
    def __init__(self, formats):
        self.resources = {}
        context = multiprocessing.get_context("fork")
        self.stopped = context.Event()
        for index, fmt in enumerate(formats):
            url = fmt["url"]
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".googlevideo.com"):
                raise RuntimeError("Section streaming requires a direct HTTPS YouTube media stream.")
            size = int(fmt.get("filesize") or 0) or None
            self.resources[f"/stream-{index}"] = {"url": url, "size": size,
                                                  "headers": fmt.get("http_headers") or {}}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def get_resource(self):
                resource = owner.resources.get(self.path)
                if resource and resource["size"] is None:
                    headers = {k: v for k, v in resource["headers"].items()
                               if k.lower() not in ("range", "host", "content-length")}
                    headers["Range"] = "bytes=0-0"
                    # Establish exact length inside the killable producer process;
                    # an estimate must never become a seek boundary or HEAD length.
                    with urlopen(Request(resource["url"], headers=headers), timeout=10) as response:
                        content_range = re.fullmatch(r"bytes 0-0/(\d+)",
                                                     response.headers.get("Content-Range", ""))
                        if response.status != 206 or not content_range or int(content_range[1]) <= 0:
                            raise RuntimeError("Cannot establish the seekable stream's exact length.")
                        if len(response.read(1)) != 1:
                            raise RuntimeError("Stream length probe ended early.")
                        resource["size"] = int(content_range[1])
                return resource

            def do_HEAD(self):
                try:
                    resource = self.get_resource()
                except Exception:
                    self.send_error(502, "Media length probe failed")
                    return
                if not resource:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(resource["size"]))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

            def do_GET(self):
                try:
                    resource = self.get_resource()
                except Exception:
                    self.send_error(502, "Media length probe failed")
                    return
                if not resource or owner.stopped.is_set():
                    self.send_error(404)
                    return
                value = self.headers.get("Range", "")
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", value) if value else None
                if value and not match:
                    self.send_error(416)
                    return
                start = int(match[1]) if match else 0
                end = int(match[2]) if match and match[2] else resource["size"] - 1
                if start < 0 or start >= resource["size"] or end < start:
                    self.send_error(416)
                    return
                position = start
                replied = False
                response = None
                failures = 0
                try:
                    while position <= end and not owner.stopped.is_set():
                        try:
                            chunk_end = min(position + CHUNK_BYTES - 1, end)
                            headers = {k: v for k, v in resource["headers"].items()
                                       if k.lower() not in ("range", "host", "content-length")}
                            headers["Range"] = f"bytes={position}-{chunk_end}"
                            response = urlopen(Request(resource["url"], headers=headers), timeout=10)
                            content_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                                         response.headers.get("Content-Range", ""))
                            if response.status != 206 or not content_range or int(content_range[1]) != position:
                                raise RuntimeError("Source did not honor the bounded byte request.")
                            resource["size"] = int(content_range[3])
                            actual_end = int(content_range[2])
                            if actual_end > chunk_end or actual_end < position:
                                raise RuntimeError("Source returned an unexpected byte range.")
                            end = min(end, resource["size"] - 1) if match and match[2] else resource["size"] - 1
                            if not replied:
                                self.send_response(206 if match else 200)
                                self.send_header("Content-Length", str(end - start + 1))
                                self.send_header("Accept-Ranges", "bytes")
                                self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
                                if match:
                                    self.send_header("Content-Range", f"bytes {start}-{end}/{resource['size']}")
                                self.end_headers()
                                replied = True
                            needed = actual_end - position + 1
                            while needed > 0 and not owner.stopped.is_set():
                                data = response.read(min(65536, needed))
                                if not data:
                                    raise RuntimeError("Source range ended early.")
                                try:
                                    self.wfile.write(data)
                                except ConnectionResetError as error:
                                    raise BrokenPipeError("Local consumer disconnected") from error
                                position += len(data)
                                needed -= len(data)
                            response.close()
                            response = None
                            failures = 0
                        except BrokenPipeError:
                            raise
                        except Exception as error:
                            if response is not None:
                                response.close()
                                response = None
                            failures += 1
                            reason = f"HTTP {error.code}" if hasattr(error, "code") else type(error).__name__
                            if failures > 3 or getattr(error, "code", None) in (401, 403, 404):
                                print(f"Media fetch failed at byte {position}: {reason}.", file=sys.stderr, flush=True)
                                raise
                            print(f"Media fetch interrupted at byte {position}: {reason}; retry {failures}/3.", file=sys.stderr, flush=True)
                            if owner.stopped.wait(min(failures, 3)):
                                break
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    if not replied:
                        self.send_error(502, "Media range fetch failed")
                finally:
                    if response is not None:
                        response.close()
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        # An isolated process lets cutoff cleanup stop sockets and DNS requests
        # as well as ffmpeg, even if an upstream worker is blocked.
        self.process = context.Process(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.1}, daemon=True,
                                       name="ytfzf-media-ranges")

    def __enter__(self):
        self.process.start()
        return [f"http://127.0.0.1:{self.server.server_port}{path}" for path in self.resources]

    def __exit__(self, *_args):
        self.stopped.set()
        self.process.terminate()
        self.process.join(timeout=1)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=1)
        self.server.server_close()
