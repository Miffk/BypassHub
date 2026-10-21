import ssl

from bypasshub import netcheck, tls


def test_bundled_roots_when_windows_store_is_empty(monkeypatch):
    """В хранилище Windows нет нужного корневого сертификата — контекст всё равно знает корневые certifi."""
    def empty_store():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    monkeypatch.setattr(ssl, "create_default_context", empty_store)
    tls.context.cache_clear()
    try:
        ctx = tls.context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
        assert len(ctx.get_ca_certs()) > 50
    finally:
        tls.context.cache_clear()


def test_netcheck_pins_tls_version():
    ctx = netcheck._context(netcheck.TLS13)
    assert ctx.minimum_version == ctx.maximum_version == ssl.TLSVersion.TLSv1_3
    ctx = netcheck._context(netcheck.TLS12)
    assert ctx.minimum_version == ctx.maximum_version == ssl.TLSVersion.TLSv1_2
    assert len(ctx.get_ca_certs()) > 50
