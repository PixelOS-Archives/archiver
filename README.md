# PixelOS SourceForge to GitHub Releases Mirror

This repository automates the mirroring of large Android ROMs from the [PixelOS SourceForge](https://sourceforge.net/projects/pixelos-releases/files/) over to GitHub Releases. GitHub has arbitrary runner limitations, so this tool incrementally downloads batches of 10 ROMs and pushes them to GitHub Releases.

## Features
- **Incremental Resuming**: Baches 10 files per standard run to prevent hitting the 6 hour GitHub Actions limit length.
- **Large File Handling**: Files > 1.9GB are dynamically chunked into standard 1.6GB (`.part`) assets securely.
- **Data Deduplication**: Avoids mirroring files already synchronized using a continuously updated `log.json`.

## Structure Layout Mappings
A SourceForge format like:
`/fourteen/RMX2020/PixelOS_RMX2020-14.0-XXX.zip`

Becomes a Github Release under the **RMX2020-fourteen** tag containing the ZIP file.
