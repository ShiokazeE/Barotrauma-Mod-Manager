# Security policy

## Reporting a vulnerability

Please do not publish secrets or a working exploit in a public issue. Open a private GitHub Security Advisory if private vulnerability reporting is enabled for this repository. Otherwise, contact the maintainer through the GitHub profile and share only the minimum technical details required to reproduce the issue.

## Sensitive data

Never attach an unredacted `baro_profiles.json`, Steam API key, DPAPI file, account token, SteamCMD cache or personal game path. Redact secrets and account identifiers from logs.

## Scope

Security-sensitive areas include ZIP extraction, direct-download URL validation, SteamCMD process execution, local file deletion, profile persistence and handling of the Steam Web API key.