# 08 · v1 Data Import

## 10. Import job

`import_v1` is a one-time job executed inside the target profile, which must be empty (no Folders). Inputs: the path of the v1 SQLite file (mounted into the container) and a Folder path mapping table (v1 Windows path → container path). With several v1 databases, create one profile each and import once per profile. The v1 schema, enum values, and reading pitfalls are in Appendix A.

| v1 table | Import rule |
|---|---|
| Folder | Keep id and name; CompareImageWith → compare_scope; IsSealed → role (true → archive, false → inbox). Replace path through the mapping table. |
| IgnoredDirectory | Keep. |
| Extension | Keep; IsImage → media_type via the default table; entries with IsImage = false that are video extensions become video. |
| File | Keep id, path, SHA-1; drop pHash. Files in state Computed become `new` (vectors must be recomputed, but the SHA-1 exists and is not computed again). |
| SameFile | Bucket by Sha1Hash and generate `source = sha1` Pairs; edges touching a file with IsIgnored = true are marked `related_keep`. |
| SimilarFile | **Not imported by default.** Similarity is rediscovered by the new model and reviewed from scratch. When the optional switch ([11 §15](11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments)) is on: generate `source = import` Pairs; IgnoredMode HiddenButConnected → `judgment = related_keep`; HiddenAndDisconnected → `unrelated`; Effective → `none`. distance is null and `model_id = null`; once the new model has vectors, the match job changes source to `vector` and fills in distance, keeping the judgment. All carry `judgment_origin = import_v1`. |

Usable immediately after import: Folder configuration and byte-identical group resolution. Vector-related data fills in gradually in the background. The import enqueues one full scan at the end to reconcile disk and records.

---

## Appendix A. v1 database schema and import pitfalls

The v1 database is one SQLite file created by `../ImageStore/ImageStore/Database/SqliteSchema.cs`. Import reads it and never modifies it.

### A.1 Tables

```
Folder            Id BLOB PK, Name TEXT NOCASE UNIQUE, Path TEXT NOCASE,
                  CompareImageWith INTEGER, IsSealed INTEGER
Extension         Id BLOB PK, Extension TEXT NOCASE UNIQUE, IsImage INTEGER, Ignored INTEGER
File              Id BLOB PK, FolderId BLOB → Folder, Path TEXT NOCASE, FileName TEXT NOCASE,
                  ExtensionId BLOB → Extension, ImageHash BLOB NULL, Sha1Hash BLOB NULL,
                  FileSize INTEGER (default -1), FileState INTEGER, ImageComparedThreshold REAL
IgnoredDirectory  Id BLOB PK, FolderId BLOB → Folder, Directory TEXT NOCASE,
                  IsSubDirectoryIncluded INTEGER
SameFile          Id BLOB PK, Sha1Hash BLOB, FileId BLOB → File (UNIQUE), IsIgnored INTEGER
SimilarFile       Id BLOB PK, File1Id BLOB → File, File2Id BLOB → File,
                  DifferenceDegree REAL, IgnoredMode INTEGER
```

### A.2 Enum values

| Column | Values |
|---|---|
| `Folder.CompareImageWith` | 0 = All, 1 = FilesInOtherDirectories, 2 = FilesInOtherFolders. Maps to `all` / `other_directories` / `other_folders`. |
| `Folder.IsSealed` | 0 / 1. 1 → `role = archive`, 0 → `inbox`. |
| `Extension.IsImage` | 0 / 1. 1 means v1 computed pHash for it. |
| `Extension.Ignored` | 0 / 1. 1 → `enabled = false`. |
| `File.FileState` | 0 = New, 1 = NotImage, 2 = NotReadable, 254 = SizeZero, 255 = Computed. Everything becomes `new` on import while keeping Sha1Hash; NotReadable may be kept as `unreadable` to avoid retries. The implementation decides and records the choice. |
| `SameFile.IsIgnored` | 0 / 1. 1 means the file is byte-identical to others but the user chose to keep it. |
| `SimilarFile.IgnoredMode` | 0 = Effective, 1 = HiddenButConnected (related, no action), 2 = HiddenAndDisconnected (unrelated). |

### A.3 Reading pitfalls

- **Guid byte order.** Every `Id` is a 16-byte BLOB written by .NET `Guid.ToByteArray()`, which is mixed-endian (first three groups little-endian). In Python read it with `uuid.UUID(bytes_le=blob)`; `uuid.UUID(bytes=blob)` yields a different id. VisualStore keeps v1 ids, so a wrong read breaks every future cross-reference.
- **Paths are split in three.** `File.Path` is the directory path relative to the Folder, without leading or trailing separator, empty string for the root; `File.FileName` has no extension; the extension is in `Extension.Extension` without the dot. Full path = `Folder.Path` + `\` + `Path` + `\` + `FileName` + `.` + `Extension` (skip the middle segment when Path is empty). Separators are Windows backslashes; convert to `/` on import.
- **`Folder.Path` is an absolute Windows path** (such as `D:\Images`); replace it with the container path from the user-supplied mapping.
- **Case.** All v1 path columns are `COLLATE NOCASE`; the VisualStore unique index is NOCASE too, so semantics match. Both fold ASCII only.
- **ImageHash** is a 40-byte pHash digest, unused, dropped. `ImageComparedThreshold` is dropped too.
- **`SameFile.Sha1Hash`** equals `File.Sha1Hash`, a 20-byte BLOB. Files may have a non-null `File.Sha1Hash` without a `SameFile` row, because v1 only created SameFile rows after Compare ran. Import rebuckets from `File.Sha1Hash` and does not depend on the SameFile table, reading only `IsIgnored` from it.
- **`SimilarFile.File1Id` / `File2Id` have no ordering convention**; the same pair may appear in either direction, in theory in both. When import is enabled, normalise to `file_a < file_b` and deduplicate; on conflict take the larger IgnoredMode (a human mark beats Effective).
- **Asymmetric cascades**: `File`, `IgnoredDirectory`, and `SameFile` cascade from their parents; `SimilarFile` does not. The database may hold SimilarFile rows pointing at deleted files; skip and count them.

### A.4 v1 difference degree

`DifferenceDegree = 1 - cross-correlation coefficient`, in [0, 1], smaller is more similar, non-linear; above 0.03 most pairs are visually unrelated. It is not comparable with VisualStore's cosine distance. The eval job ([05 §7.6](05-pipeline.md#76-model-and-thresholds)) uses only IgnoredMode as labels, never this number.

---

## Appendix B. Files worth reading in the v1 repository

Paths are relative to `../ImageStore/`.

| File | What to look for |
|---|---|
| `CLAUDE.md` | v1 overall description: data model, enums, cascade rules, known pitfalls. |
| `doc/walkthrough/case.md` | The user's actual workflow with four Folders; the prototype of the VisualStore product flow. |
| `doc/concept/*.md` | Concept definitions for Folder, File, SameFile, SimilarFile, DifferenceDegree. |
| `doc/cmdlet/SameFile/SelectSameFile.md` | Auto-selection rules for byte-identical groups, generalised in [06 §8.2](06-review-ui.md#82-default-resolution-rules). |
| `doc/cmdlet/SimilarFile/ResolveSimilarFiles.md` | Every feature of the v1 similarity review UI; the group detail page should cover whatever is still valuable. |
| `ImageStore/Database/SqliteSchema.cs` | Table definitions. |
| `ImageStore/Folder/SyncFolderCmdlet.cs` | How v1 scanning splits paths and handles IgnoredDirectory. |
| `ImageStore/SimilarFile/CompareSimilarFilesCmdlet.cs` | How v1 implements the three CompareImageWith exclusion levels; [05 §7.4](05-pipeline.md#74-match) step 4 must match these semantics. |
| `ImageStore/SimilarFile/SimilarFileInGroupManager.cs` | v1 grouping and three-state judgment; the source of the connectivity semantics. |
| `ImageStore/File/FileHelper.cs` | The order of database and disk operations when v1 deletes or moves files. |
