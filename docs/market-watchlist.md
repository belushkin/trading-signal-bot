# Stellar market watchlist verification

The packaged watchlist fixes issuer identity; it is not an endorsement of an issuer,
redemption, liquidity, or trading strategy. Tier 2 is disabled by default.
`BOT_WATCHLIST_ENABLED` selects entries by code from these exact identities, not
arbitrary issuers. A non-empty `BOT_MONITORED_PAIRS` overrides the selection.
USDC/VELO, USDC/SHX and USDC/AQUA are also monitored when both respective
assets are enabled, using the same identities below. Their market data has its
own observation history; the liquidity measurements below concern XLM pairs.

| Pair | Tier | Quote asset code and issuer | Identity source |
| --- | --- | --- | --- |
| XLM/USDC | 1 | `USDC:GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN` | [Centre TOML](https://centre.io/.well-known/stellar.toml) |
| XLM/SHX | 1 | `SHX:GDSTRSHXHGJ7ZIVRBXEYE5Q74XUVCUSEKEBR7UCHEUUEK72N7I7KJ6JH` | [Stronghold TOML](https://stronghold.co/.well-known/stellar.toml) |
| XLM/VELO | 1 | `VELO:GDM4RQUQQUVSKQA7S6EM7XBZP3FCGH4Q7CL6TABQ7B2BEJ5ERARM2M5M` | [Velo TOML](https://velo.org/.well-known/stellar.toml) |
| XLM/AQUA | 1 | `AQUA:GBNZILSTVQZ4R7IKQDGHYGY2QXL5QOFJYQMXPKWRRM5PAV7Y4M67AQUA` | [Aquarius TOML](https://aqua.network/.well-known/stellar.toml) |
| XLM/BTC | 2 | `BTC:GDPJALI4AZKUU2W426U5WKMAT6CN3AJRPIIRYR2YM54TL2GDWO5O2MZM` | [Ultra Capital TOML](https://ultracapital.xyz/.well-known/stellar.toml) |
| XLM/XRP | 2 | `XRP:GBXRPL45NPHCVMFFAYZVUVFFVKSIZ362ZXFP7I2ETNQ3QKZMFLPRDTD5` | [Fchain TOML](https://fchain.io/.well-known/stellar.toml) |

XLM is the Stellar native asset. BTC and XRP are the named issuers' Stellar
tokens, not native Bitcoin or XRP Ledger assets. Other issuers with the same
codes were not substituted.

## Public Horizon observation

Measured 2026-11-09 approximately 19:59–20:00 UTC using
`https://horizon.stellar.org`. All six exact pairs returned recent trades and
two-sided order books. Hourly trade aggregations below cover completed hours
ending 19:00 UTC. Aggregation type is not separated by this measurement: do
not interpret it as order-book-only or as independently measured AMM volume.
The 24-hour and 7-day counts are sums of returned buckets, not a guarantee of
complete coverage; BTC returned 23/24 and 159/168 buckets respectively, so
its missing buckets are not assumed to be zero.

| Pair | 24h observed XLM / trades | 7d observed XLM / trades | Bid / ask (quote per XLM) | Spread | Last trade UTC |
| --- | ---: | ---: | ---: | ---: | --- |
| USDC | 9,540,156 / 47,715 | 48,932,893 / 483,647 | 0.1470800 / 0.1471596 | 0.0541% | 19:58:47 |
| SHX | 766,514 / 8,866 | 4,668,445 / 57,907 | 34.6400723 / 34.7546841 | 0.3298% | 19:58:37 |
| VELO | 198,899 / 6,930 | 2,062,658 / 61,094 | 42.5522700 / 42.6584336 | 0.2489% | 20:00:07 |
| AQUA | 70,950 / 3,160 | 750,399 / 41,891 | 596.7658957 / 598.4202767 | 0.2765% | 19:57:07 |
| BTC | 93,978 / 743 | 214,580 / 3,009 | 0.00000190645 / 0.00000207054 | 7.9250% | 19:42:32 |
| XRP | 325,769 / 7,577 | 2,127,361 / 77,671 | 0.1344395 / 0.1345516 | 0.0833% | 19:55:42 |

The maximum 20 returned levels had these raw bid/ask totals, respectively:
USDC 17,066.60 / 413,934.54; SHX 2,068,518.49 / 40,710.65; VELO
907,243.34 / 15,415.08; AQUA 10,722,320.30 / 254,912.26; BTC 0.011609 /
5,034.14; XRP 2,198.55 / 103,309.23. Horizon bid amounts are in the quote
asset and ask amounts in XLM; these raw sides are **not comparable** without
converting each bid level at its own price. These are top-20 snapshots, not
total market liquidity. The observation counts above are returned hourly
aggregation buckets, not bot collection cycles. The latest ten SHX trades
included both order-book and liquidity-pool trades; the BTC sample included
liquidity-pool trades. A subsequent venue-filtered Horizon query at 20:20 UTC
found both types for each pair. The latest order-book / liquidity-pool trade
times (UTC, 2026-11-09) were: USDC 20:20:52 / 20:03:42; SHX 20:20:47 /
19:51:52; VELO 20:20:32 / 20:20:32; AQUA 20:19:57 / 19:38:32; BTC
02:21:37 / 20:08:32; XRP 20:18:42 / 19:39:42. This confirms both venue types
have trades, but does not establish their volume split. AMM reserves and
separate AMM volume were not measured.

The dashboard uses persisted collector observations, not the table above. A
fresh two-sided snapshot alone does not establish a baseline. The new spread and
price rules require continuous sampled order-book history as described in the
README. The volume rule requires newly recorded successful trade scans and Horizon
ledger watermarks; legacy observations cannot establish ingestion coverage. Large-trade
alerts also require a same-pair, same-venue history and an absolute XLM minimum.
Depth-change alerts are not implemented because top-20 book snapshots do not
cover a consistent price range. Data availability is not evidence
that a signal is useful. Old trade types are unknown and historical bid-depth
values before the unit correction are not displayed. Prices in older snapshots
were rounded by Horizon, older snapshot source URLs used unsupported parameters,
and historical signals have not been recalculated.
