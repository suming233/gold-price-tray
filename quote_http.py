"""Public quote requests: direct connections, independent of desktop proxies."""
import urllib.request


def open_quote(request, timeout=8):
    # A desktop/launcher proxy must not break public market data requests.
    # Use a fresh opener for each worker rather than a mutable global opener.
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({})).open(request, timeout=timeout)
