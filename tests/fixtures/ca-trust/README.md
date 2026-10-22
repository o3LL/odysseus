# Public certificate fixtures

`incident.json` contains public certificates and twelve incident TLS chains from the public eMudhra incident inventory. It contains no private keys. Source URLs and the original archive digest are recorded in [the image documentation](../../../docs/searxng-trust-image.md). Chain verification uses the fixture's fixed Unix timestamp to avoid certificate expiry changing the regression result. Image validation checks each stored DER fingerprint before use.

`emsign-g1.txt` is the unaffected public emSign Root CA - G1 certificate in PEM format, used to verify preservation of unrelated trust. Its fingerprint is SHA-256 `40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367`.
