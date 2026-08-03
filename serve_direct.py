from http.server import ThreadingHTTPServer

from server import Handler
from standalone_core.fingerprint_store import optimize_fingerprint_store


class QuietHandler(Handler):
    def log_message(self, _format, *_args):
        return


if __name__ == "__main__":
    optimize_fingerprint_store("US")
    ThreadingHTTPServer(("127.0.0.1", 5601), QuietHandler).serve_forever()
