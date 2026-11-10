# Repository maintainer checklist

This repository is already created at [ShiokazeE/Barotrauma-Mod-Manager](https://github.com/ShiokazeE/Barotrauma-Mod-Manager). This document covers the remaining one-time project settings and release procedure.

## Repository description

Suggested About description:

> A desktop Steam Workshop and load-order manager for Barotrauma. Browse mods and collections, install content, and manage author load-order presets.

Suggested topics:

- `barotrauma`
- `mod-manager`
- `steam-workshop`
- `python`
- `windows`
- `gaming`
- `tkinter`

## GitHub Pages

The landing page is in `docs/`.

1. Open **Settings → Pages**.
2. Under **Build and deployment**, choose **Deploy from a branch**.
3. Select branch `main` and folder `/docs`.
4. Save and wait for the Pages deployment check to complete.

Expected URL: https://shiokazee.github.io/Barotrauma-Mod-Manager/

The site links to the README files in GitHub because the repository root is not part of the published `/docs` folder.

## Actions permissions

The Windows release workflow requests only `contents: write`, which is required to publish a GitHub Release from a version tag. CI uses `contents: read`.

Keep **Settings → Actions → General → Workflow permissions** at the least-privilege level that permits the workflow to run. Do not add secrets for Steam: the build does not need a Steam API key.

## First Windows release

Do not publish a release until a Windows executable has been built and smoke-tested.

1. Run the **Build Windows** workflow manually from the reviewed branch.
2. Download the `Barotrauma-Mod-Manager-Windows` artifact and test it on a clean Windows account or machine.
3. Verify startup, Workshop configuration, SteamCMD bootstrap, install/update/remove, profile switching, ModLists XML import/export, and applying a profile to the game.
4. After the tested commit is merged to `main`, create a version tag such as `v2.7.1` on that commit.
5. Pushing the tag builds the portable ZIP and publishes the GitHub Release.

## Project policy

- Keep the application free to use; donations are voluntary and must not unlock core features.
- Do not commit API keys, user profile JSON, DPAPI files, SteamCMD caches, LocalMods or game configuration files.
- Keep the license unset until the project owner explicitly chooses licensing terms.
- Add screenshots only when they are captured from the real application.
