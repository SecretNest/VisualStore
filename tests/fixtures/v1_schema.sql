CREATE TABLE Folder (Id BLOB PRIMARY KEY, Name TEXT COLLATE NOCASE UNIQUE, Path TEXT COLLATE NOCASE,
                     CompareImageWith INTEGER, IsSealed INTEGER);
CREATE TABLE Extension (Id BLOB PRIMARY KEY, Extension TEXT COLLATE NOCASE UNIQUE, IsImage INTEGER, Ignored INTEGER);
CREATE TABLE File (Id BLOB PRIMARY KEY, FolderId BLOB REFERENCES Folder(Id) ON DELETE CASCADE,
                   Path TEXT COLLATE NOCASE, FileName TEXT COLLATE NOCASE,
                   ExtensionId BLOB REFERENCES Extension(Id), ImageHash BLOB, Sha1Hash BLOB,
                   FileSize INTEGER DEFAULT -1, FileState INTEGER, ImageComparedThreshold REAL);
CREATE TABLE IgnoredDirectory (Id BLOB PRIMARY KEY, FolderId BLOB REFERENCES Folder(Id) ON DELETE CASCADE,
                               Directory TEXT COLLATE NOCASE, IsSubDirectoryIncluded INTEGER);
CREATE TABLE SameFile (Id BLOB PRIMARY KEY, Sha1Hash BLOB, FileId BLOB UNIQUE REFERENCES File(Id) ON DELETE CASCADE,
                       IsIgnored INTEGER);
CREATE TABLE SimilarFile (Id BLOB PRIMARY KEY, File1Id BLOB, File2Id BLOB, DifferenceDegree REAL, IgnoredMode INTEGER);
