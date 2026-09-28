"""Complaint intake: free text (an NCRP complaint, an email, a chat export) -> the wallet addresses in
it, validated, grouped by chain. main.py turns each chain's group into one multi-wallet case, which
is what makes /convergence reachable from a paste instead of hand-picked traces.

Validation at the trust boundary, stdlib only:
  BTC 1…/3…   base58check (double-SHA256 checksum)         a typo fails here
  BTC bc1…    bech32 / bech32m checksum (BIP-173 / BIP-350)
  Tron T…     base58check, version byte 0x41
  EVM 0x…     shape only. ponytail: EIP-55 mixed-case checksum needs keccak-256, which is not in
              hashlib (sha3_256 is a different padding); add a keccak dep if typo'd EVM addresses
              start costing provider calls. Reported as checksum "unverified", never as valid.
An EVM address is ambiguous across eth/polygon; intake files it under eth (where the labels are).
"""
import hashlib
import re

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
# Word boundaries so an address is not carved out of a longer token (a tx hash, a URL path).
_PATTERNS = [
    ("eth", re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{40}(?![0-9A-Za-z])")),
    ("tron", re.compile(r"(?<![0-9A-Za-z])T[1-9A-HJ-NP-Za-km-z]{33}(?![0-9A-Za-z])")),
    ("btc", re.compile(r"(?<![0-9A-Za-z])(?:[13][1-9A-HJ-NP-Za-km-z]{25,34}|bc1[02-9ac-hj-np-z]{11,71})(?![0-9A-Za-z])",
                       re.I)),
]


def _b58check(s: str) -> bytes | None:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    raw = b"\0" * (len(s) - len(s.lstrip("1"))) + raw
    body, check = raw[:-4], raw[-4:]
    return body if len(raw) > 4 and hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] == check else None


def _bech32_ok(s: str) -> bool:
    s = s.lower()
    hrp, data = s[:s.rfind("1")], [_BECH.index(c) for c in s[s.rfind("1") + 1:]]
    gen, chk = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3], 1
    for v in [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data:
        b, chk = chk >> 25, (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (b >> i) & 1 else 0
    return chk in (1, 0x2bc830a3)   # bech32 (witness v0) | bech32m (v1+, taproot)


def check(chain: str, addr: str) -> tuple[bool, str]:
    if chain == "eth":
        return True, "shape ok; EIP-55 checksum unverified"
    if chain == "btc" and addr[:3].lower() == "bc1":
        return (_bech32_ok(addr), "bech32 checksum") if addr in (addr.lower(), addr.upper()) else \
            (False, "mixed-case bech32")
    body = _b58check(addr)
    if body is None:
        return False, "base58check checksum failed"
    if chain == "tron":
        return (body[:1] == b"\x41" and len(body) == 21), "base58check, Tron version byte"
    return (body[:1] in (b"\x00", b"\x05") and len(body) == 21), "base58check, BTC version byte"


def extract(text: str) -> list[dict]:
    """Every address in text, first-seen order, deduped per chain, with its validation verdict."""
    seen, out = set(), []
    hits = sorted((m.start(), chain, m.group(0)) for chain, rx in _PATTERNS for m in rx.finditer(text))
    for _, chain, addr in hits:
        key = (chain, addr.lower() if chain == "eth" or addr[:3].lower() == "bc1" else addr)
        if key in seen:
            continue
        seen.add(key)
        ok, why = check(chain, addr)
        out.append({"address": addr, "chain": chain, "valid": ok, "check": why})
    return out


def _selfcheck():
    txt = ("Sample text. Paid 0.5 BTC to 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa and bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq, "
           "then USDT to TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t and 0x7F367cC41522cE07553e823bf3be79A889DEbe1B; "
           "again 0x7f367cc41522ce07553e823bf3be79a889debe1b. Typo: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb. "
           "Tx 0x" + "ab" * 32)
    got = {(r["chain"], r["address"]): r["valid"] for r in extract(txt)}
    assert got[("btc", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")] is True
    assert got[("btc", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")] is False           # one-char typo caught
    assert got[("btc", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")] is True    # BIP-173 test vector
    assert got[("tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")] is True
    assert sum(c == "eth" for c, _ in got) == 1                                   # case-insensitive dedupe
    assert not any(a.startswith("0xabab") for _, a in got)                        # a tx hash is not an address
    print("intake selfcheck PASS")


if __name__ == "__main__":
    _selfcheck()
