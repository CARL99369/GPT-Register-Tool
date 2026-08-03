# Kakao Seed Derivation Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kakao reuse fixed same-country proxies and correctly derive `sid`/`sessid` proxies without sticky identity false positives.

**Architecture:** Keep the change inside `kakao_extract.py`. Treat an all-same-country chain as a valid fixed-proxy chain, avoid rewriting selectors that already match, and compute derived identity by removing only the country tag explicitly added during that derivation.

**Tech Stack:** Python 3, `unittest`/pytest, curl-cffi runtime, .NET 10 WPF build verification.

---

## File map

- Modify `services/protocol-payment/kakao/kakao_extract.py`: session selector parsing, same-country reuse, cross-country identity verification.
- Modify `tests/test_kakao_extract.py`: focused regression tests for fixed KR, `sessid`, legacy `sid`, and invalid fixed cross-country chains.

### Task 1: Add failing Kakao Seed regression tests

**Files:**
- Modify: `tests/test_kakao_extract.py`
- Test: `tests/test_kakao_extract.py`

- [ ] **Step 1: Add the fixed-country and session-field regression cases**

Add a `ProxyDerivationTests` class using synthetic credentials only:

```python
class ProxyDerivationTests(unittest.TestCase):
    def test_all_kr_fixed_proxy_reuses_seed_without_country_selector(self):
        seed = "socks5h://customer-sessid-demo123:pass@gw.example:9999"
        with (
            patch.object(kakao, "CHECKOUT_COUNTRY", "KR"),
            patch.object(kakao, "PROMOTION_COUNTRY", "KR"),
            patch.object(kakao, "PROVIDER_COUNTRY", "KR"),
        ):
            chain = kakao.kakao_proxy_chain(seed)

        normalized = kakao.normalize_proxy_url(seed)
        self.assertEqual(chain, (normalized, normalized, normalized))

    def test_all_kr_sessid_with_natural_uppercase_suffix_is_unchanged(self):
        seed = "socks5h://customer-country-kr-sessid-demoON:pass@gw.example:9999"
        with (
            patch.object(kakao, "CHECKOUT_COUNTRY", "KR"),
            patch.object(kakao, "PROMOTION_COUNTRY", "KR"),
            patch.object(kakao, "PROVIDER_COUNTRY", "KR"),
        ):
            chain = kakao.kakao_proxy_chain(seed)

        normalized = kakao.normalize_proxy_url(seed)
        self.assertEqual(chain, (normalized, normalized, normalized))

    def test_cross_country_sessid_derivation_preserves_seed_identity(self):
        seed = "socks5h://customer-country-kr-sessid-demoON:pass@gw.example:9999"
        with (
            patch.object(kakao, "CHECKOUT_COUNTRY", "KR"),
            patch.object(kakao, "PROMOTION_COUNTRY", "VN"),
            patch.object(kakao, "PROVIDER_COUNTRY", "KR"),
        ):
            checkout, promotion, provider = kakao.kakao_proxy_chain(seed)

        self.assertEqual(checkout, kakao.normalize_proxy_url(seed))
        self.assertEqual(provider, kakao.normalize_proxy_url(seed))
        decoded = kakao.unquote(promotion)
        self.assertIn("country-vn", decoded)
        self.assertIn("sessid-demoONVN", decoded)
        self.assertEqual(
            kakao.proxy_chain_key(seed),
            kakao.proxy_chain_key(promotion, derived_country="VN"),
        )

    def test_cross_country_legacy_sid_derivation_remains_supported(self):
        seed = "socks5h://customer-country-kr-sid-demo123:pass@gw.example:9999"
        with (
            patch.object(kakao, "CHECKOUT_COUNTRY", "KR"),
            patch.object(kakao, "PROMOTION_COUNTRY", "VN"),
            patch.object(kakao, "PROVIDER_COUNTRY", "KR"),
        ):
            _, promotion, _ = kakao.kakao_proxy_chain(seed)

        self.assertIn("sid-demo123VN", kakao.unquote(promotion))

    def test_cross_country_fixed_proxy_without_selector_is_rejected(self):
        seed = "socks5h://customer-sessid-demo123:pass@gw.example:9999"
        with (
            patch.object(kakao, "CHECKOUT_COUNTRY", "KR"),
            patch.object(kakao, "PROMOTION_COUNTRY", "VN"),
            patch.object(kakao, "PROVIDER_COUNTRY", "KR"),
        ):
            with self.assertRaisesRegex(RuntimeError, "country/region"):
                kakao.kakao_proxy_chain(seed)
```

- [ ] **Step 2: Run the focused tests and verify the expected failures**

Run:

```powershell
python -m pytest tests/test_kakao_extract.py::ProxyDerivationTests -q
```

Expected: the fixed KR and natural-uppercase `sessid` cases fail under the current implementation; the failure must come from missing selector or sticky-chain identity logic.

### Task 2: Implement minimal same-country reuse and derivation-aware identity

**Files:**
- Modify: `services/protocol-payment/kakao/kakao_extract.py`
- Test: `tests/test_kakao_extract.py`

- [ ] **Step 1: Replace the ambiguous session regex**

Define one boundary-aware selector that recognizes the complete `sessid` and `sid` names:

```python
_PROXY_SESSION_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<name>sessid|sid)(?P<separator>[-_=])(?P<value>[A-Za-z0-9]+)"
)
```

- [ ] **Step 2: Make chain identity derivation-aware**

Change `proxy_chain_key` to accept `derived_country=""`. It must always normalize `country/region`; only when `derived_country` is supplied may it remove exactly that suffix from `sid` or `sessid`:

```python
def proxy_chain_key(proxy: str, derived_country: str = "") -> str:
    normalized = unquote(normalize_proxy_url(proxy))
    normalized = _PROXY_COUNTRY_SELECTOR_RE.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}*",
        normalized,
    )
    country_tag = str(derived_country or "").strip().upper()
    if country_tag:
        def strip_derived_tag(match: re.Match[str]) -> str:
            value = match.group("value")
            if value.upper().endswith(country_tag) and len(value) > len(country_tag):
                value = value[:-len(country_tag)]
            return f"{match.group('name')}{match.group('separator')}{value}"

        normalized = _PROXY_SESSION_RE.sub(strip_derived_tag, normalized)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16] if normalized else ""
```

- [ ] **Step 3: Avoid no-op country rewrites**

In `proxy_for_country`, count selectors separately from changed selectors. Return the normalized source proxy unchanged when all selectors already equal the target. Append the country tag through `_PROXY_SESSION_RE` only when a selector actually changed.

- [ ] **Step 4: Reuse fixed proxies only for an all-same-country chain**

In `kakao_proxy_chain`, normalize the Seed and inspect its decoded username/password for a country selector. If no selector exists and the three configured countries are identical, return the normalized Seed three times. If countries differ, raise the existing clear selector error.

Validate changed proxies with `proxy_chain_key(proxy, derived_country=country)` and unchanged proxies with `proxy_chain_key(proxy)`.

- [ ] **Step 5: Run focused tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_kakao_extract.py::ProxyDerivationTests -q
```

Expected: `5 passed`.

- [ ] **Step 6: Run the complete Kakao unit tests**

Run:

```powershell
python -m pytest tests/test_kakao_extract.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit the focused fix**

```powershell
git add -- services/protocol-payment/kakao/kakao_extract.py tests/test_kakao_extract.py
git commit -m "fix: support fixed Kakao sessid proxy seeds"
```

### Task 3: Regression and desktop build verification

**Files:**
- Verify: `services/protocol-payment/kakao/kakao_extract.py`
- Verify: `tests/test_kakao_extract.py`
- Build: `SmsWorkbench/SmsWorkbench.csproj`

- [ ] **Step 1: Run the full Python test suite**

Run:

```powershell
python -m pytest -q
```

Expected: zero failures.

- [ ] **Step 2: Run the desktop test project**

Run:

```powershell
dotnet test tests/SmsWorkbench.Tests/SmsWorkbench.Tests.csproj -c Release
```

Expected: zero failed tests.

- [ ] **Step 3: Publish the desktop application**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\SmsWorkbench\build_dotnet.ps1
```

Expected: exit code 0 and an updated `dist/net10/SmsWorkbench.exe`.

- [ ] **Step 4: Inspect the final diff and repository status**

Run:

```powershell
git diff HEAD~1 --check
git status --short
```

Expected: no whitespace errors; generated `dist` changes are reported accurately and source changes match the design.
