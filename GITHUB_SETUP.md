# One-time GitHub setup

The repository contents are prepared for `ShiokazeE/Barotrauma-Mod-Manager`.

## Create the empty repository

1. Open https://github.com/new
2. Repository name: `Barotrauma-Mod-Manager`
3. Description: `A desktop Steam Workshop and load-order manager for Barotrauma.`
4. Choose **Public**.
5. Enable **Add a README file** so GitHub creates the initial `main` branch.
6. Do not select a license yet; the project owner has not chosen one.
7. Create repository and share its URL with the maintainer/assistant.

The connected GitHub integration can then add and update text files, workflow definitions and documentation on the default branch. Binary screenshot assets can be uploaded from this prepared folder if required.

## Repository About section

Suggested description:

`A desktop Steam Workshop and load-order manager for Barotrauma. Browse mods and collections, install content, and manage author load-order presets.`

Suggested topics:

`barotrauma`, `mod-manager`, `steam-workshop`, `python`, `windows`, `gaming`, `tkinter`

## Enable the project website

After the repository files have been added:

1. Open **Settings → Pages**.
2. Under **Build and deployment**, choose **Deploy from a branch**.
3. Choose branch `main` and folder `/docs`.
4. Save. GitHub Pages will publish `https://shiokazee.github.io/Barotrauma-Mod-Manager/` after deployment completes.

## First Windows release

After a Windows build has been tested, create and push a version tag such as `v2.7.1`. The workflow in `.github/workflows/build-windows.yml` builds the executable and creates a GitHub Release for version tags.